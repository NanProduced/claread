from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import Agent

from app.config.settings import Settings, get_settings
from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.database import connection as db_connection
from app.llm.agent_runner import extract_run_usage
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.router import build_model_for_route
from app.llm.routes import MODEL_ROUTE_READER_LAYER_VOCABULARY
from app.schemas.reader_orchestration import (
    ReaderTextRangeAnchor,
    VocabularyContextGlossItem,
    VocabularyHighlightItem,
    VocabularyLayerOutput,
    VocabularyPhraseGlossItem,
)
from app.services.ai_usage import (
    BILLING_MODE_INTERNAL_ONLY,
    CAPABILITY_READER_VOCABULARY,
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
    VOCABULARY_BATCH_JOB_TYPE,
    VOCABULARY_BATCH_OPERATION_FINGERPRINT,
    VOCABULARY_BATCH_TARGET_SCOPE,
    VOCABULARY_JOB_TYPE,
    VOCABULARY_OPERATION_FINGERPRINT,
    VOCABULARY_TARGET_SCOPE,
    _fingerprint_matches_base,
)
from .job_runtime import ClaimResult, FenceViolationError, ReaderJobRuntime
from .layer_publisher import (
    PublishedVocabularyBatch,
    PublishedVocabularyLayer,
    VocabularyLayerPublisher,
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

DEFAULT_VOCABULARY_RETRY_DELAY = timedelta(minutes=5)
VOCABULARY_WORKFLOW_VERSION = "d5-v3-vocabulary-worker"
VOCABULARY_PROMPT_AGENT_NAME = "reader_layer_vocabulary"
FAKE_VOCABULARY_PROMPT_VERSION = "fake-vocabulary-worker-v1"
FAKE_VOCABULARY_MODEL_PROFILE = "fake-reader-layer-vocabulary"
FAKE_VOCABULARY_MODEL_PROVIDER = "fake-provider"
FAKE_VOCABULARY_MODEL_NAME = "fake-vocabulary-model"
MAX_VOCABULARY_ITEMS = 5
MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH = 160
MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH = 240
MAX_VOCABULARY_DIAGNOSTIC_ITEMS = 8
MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH = 80

# T7 strategy metadata keys that T5 bootstrap writes into reader_jobs.input_json.
# T7 reads them back and validates against the live resolver output. Missing
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
_VOCABULARY_LAYER_NAME = "vocabulary"
_STRATEGY_METADATA_MISSING_CODE = "strategy_metadata_missing"
_STRATEGY_HASH_MISMATCH_CODE = "strategy_hash_mismatch"
_LAYER_POLICY_HASH_MISMATCH_CODE = "layer_policy_hash_mismatch"
_STRATEGY_VERSION_MISMATCH_CODE = "strategy_version_mismatch"


@dataclass(frozen=True, slots=True)
class VocabularyAnchorSegmentContext:
    anchor_segment_id: str
    sentence_id: str
    segment_type: str
    unit_start_utf16: int
    unit_end_utf16: int
    text_hash: str
    text: str
    boundary_quality: str = "normal"


@dataclass(frozen=True, slots=True)
class VocabularyJobContext:
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
    source_text: str
    text_hash: str
    anchor_segments: tuple[VocabularyAnchorSegmentContext, ...]
    # T7 strategy fields. Populated by _load_job_context from
    # reader_jobs.input_json (written by T5 bootstrap) and cross-validated
    # against resolve_reader_variant_strategy(). Fail-closed contract:
    # missing metadata or hash mismatch never falls back to a default.
    reading_goal: str
    reading_variant: str
    strategy_version: str
    strategy_hash: str
    layer_policy_hash: str
    vocabulary_prompt_lines: tuple[str, ...]


class VocabularyCandidateItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_segment_id: str = Field(min_length=1)
    selected_text: str = Field(
        min_length=1,
        max_length=MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH,
    )


class VocabularyHighlightCandidateItem(VocabularyCandidateItemBase):
    item_type: Literal["vocab_highlight"] = "vocab_highlight"
    headword: str = Field(min_length=1, max_length=64)
    brief_explanation: str | None = Field(
        default=None,
        max_length=MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH,
    )
    reason: str | None = Field(
        default=None,
        max_length=MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH,
    )


class VocabularyPhraseGlossCandidateItem(VocabularyCandidateItemBase):
    item_type: Literal["phrase_gloss"] = "phrase_gloss"
    phrase: str = Field(min_length=1, max_length=MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH)
    phrase_type: Literal[
        "collocation",
        "phrasal_verb",
        "idiom",
        "proper_noun",
        "compound",
        "other",
    ]
    gloss: str = Field(min_length=1, max_length=MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH)
    example: str | None = Field(
        default=None,
        max_length=MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH,
    )


class VocabularyContextGlossCandidateItem(VocabularyCandidateItemBase):
    item_type: Literal["context_gloss"] = "context_gloss"
    display: str = Field(
        min_length=1,
        max_length=MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH,
    )
    gloss: str = Field(min_length=1, max_length=MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH)
    reason: str = Field(min_length=1, max_length=MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH)


VocabularyCandidateItem = Annotated[
    VocabularyHighlightCandidateItem
    | VocabularyPhraseGlossCandidateItem
    | VocabularyContextGlossCandidateItem,
    Field(discriminator="item_type"),
]


class VocabularyCandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    items: list[VocabularyCandidateItem] = Field(
        default_factory=list,
        max_length=MAX_VOCABULARY_ITEMS,
    )


class VocabularyBatchUnitCandidateOutput(BaseModel):
    """Per-unit vocabulary candidate output within a batch result.

    T1.1 short-article batch path: a single LLM call covers all units of a
    short article. Each entry pairs a ``unit_id`` with the vocabulary
    candidate items emitted for that unit.
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    items: list[VocabularyCandidateItem] = Field(
        default_factory=list,
        max_length=MAX_VOCABULARY_ITEMS,
    )


class VocabularyBatchCandidateOutput(BaseModel):
    """Structured output for the vocabulary batch LLM call.

    The model returns one ``VocabularyBatchUnitCandidateOutput`` per unit;
    the batch worker validates that the set of ``unit_id`` values exactly
    matches the batch job's ``target_unit_ids``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    units: list[VocabularyBatchUnitCandidateOutput] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class VocabularyExecutionResult:
    output: VocabularyLayerOutput
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = FAKE_VOCABULARY_PROMPT_VERSION
    model_route: str = MODEL_ROUTE_READER_LAYER_VOCABULARY
    model_profile: str | None = FAKE_VOCABULARY_MODEL_PROFILE
    model_provider: str | None = FAKE_VOCABULARY_MODEL_PROVIDER
    model_name: str | None = FAKE_VOCABULARY_MODEL_NAME
    diagnostics: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class VocabularyJobProcessResult:
    claim: ClaimResult
    context: VocabularyJobContext | None
    status: str
    output: VocabularyLayerOutput | None = None
    published_layer: PublishedVocabularyLayer | None = None
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


class VocabularyExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_class: str,
        failure_code: str,
        rationale_code: str | None = None,
        prompt_version: str | None = None,
        model_route: str = MODEL_ROUTE_READER_LAYER_VOCABULARY,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.rationale_code = rationale_code or failure_code
        self.prompt_version = prompt_version
        self.model_route = model_route
        self.model_profile = model_profile
        self.model_provider = model_provider
        self.model_name = model_name


@dataclass(slots=True)
class _ResolvedVocabularyCandidate:
    order_index: int
    item_index: int
    item_type: str
    anchor_segment_id: str
    selected_text: str
    priority: int
    resolved_item: (
        VocabularyHighlightItem
        | VocabularyPhraseGlossItem
        | VocabularyContextGlossItem
    )


class VocabularyExecutor(Protocol):
    async def generate(
        self,
        context: VocabularyJobContext,
    ) -> VocabularyExecutionResult: ...


class PydanticAIVocabularyExecutor:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _build_agent(self, *, model: Any) -> Agent:
        return Agent(
            model=model,
            output_type=VocabularyCandidateOutput,
            instructions=load_agent_instructions(VOCABULARY_PROMPT_AGENT_NAME),
            name="reader_layer_vocabulary_agent",
            retries={"tools": 1, "output": 2},
        )

    async def _run_agent(self, agent: Agent, prompt: str) -> Any:
        return await agent.run(prompt)

    async def generate(
        self,
        context: VocabularyJobContext,
    ) -> VocabularyExecutionResult:
        settings = self._settings or get_settings()
        prompt_version = get_prompt_version()
        if not str(settings.reader_vocabulary_model_profile or "").strip():
            raise VocabularyExecutionError(
                (
                    "vocabulary executor is not configured; set "
                    "reader_vocabulary_model_profile or inject an explicit fake "
                    "executor for tests"
                ),
                retryable=False,
                failure_class="configuration",
                failure_code="vocabulary_executor_unconfigured",
                prompt_version=prompt_version,
            )
        model, model_config = build_model_for_route(
            settings,
            MODEL_ROUTE_READER_LAYER_VOCABULARY,
        )
        if model is None:
            raise VocabularyExecutionError(
                "reader_layer_vocabulary model route is not configured",
                retryable=False,
                failure_class="configuration",
                failure_code="model_route_unavailable",
                prompt_version=prompt_version,
            )

        assert_real_llm_allowed(
            (
                "app.services.reader_orchestration.vocabulary_worker."
                "PydanticAIVocabularyExecutor"
            ),
            model_config=model_config,
        )

        agent = self._build_agent(model=model)
        try:
            result = await self._run_agent(agent, _build_vocabulary_prompt(context))
        except VocabularyExecutionError:
            raise
        except Exception as exc:
            raise VocabularyExecutionError(
                f"reader_layer_vocabulary agent execution failed: {exc}",
                retryable=True,
                failure_class="provider",
                failure_code=type(exc).__name__,
                prompt_version=prompt_version,
                model_profile=(
                    str(model_config.profile_name) if model_config is not None else None
                ),
                model_provider=(
                    str(model_config.provider) if model_config is not None else None
                ),
                model_name=(
                    str(model_config.model_name) if model_config is not None else None
                ),
            ) from exc

        try:
            candidate_output = VocabularyCandidateOutput.model_validate(result.output)
        except ValidationError as exc:
            raise VocabularyExecutionError(
                f"reader_layer_vocabulary produced invalid structured output: {exc}",
                retryable=False,
                failure_class="validation",
                failure_code="model_output_invalid",
                prompt_version=prompt_version,
                model_profile=(
                    str(model_config.profile_name) if model_config is not None else None
                ),
                model_provider=(
                    str(model_config.provider) if model_config is not None else None
                ),
                model_name=(
                    str(model_config.model_name) if model_config is not None else None
                ),
            ) from exc

        output, diagnostics = _build_vocabulary_output_from_candidates(
            context,
            candidate_output,
        )
        usage_data = extract_run_usage(result)
        return VocabularyExecutionResult(
            output=output,
            usage_data=usage_data,
            prompt_version=prompt_version,
            model_profile=(
                str(model_config.profile_name) if model_config is not None else None
            ),
            model_provider=(
                str(model_config.provider) if model_config is not None else None
            ),
            model_name=(
                str(model_config.model_name) if model_config is not None else None
            ),
            diagnostics=diagnostics,
        )


class FakeVocabularyExecutor:
    async def generate(
        self,
        context: VocabularyJobContext,
    ) -> VocabularyExecutionResult:
        return VocabularyExecutionResult(
            output=VocabularyLayerOutput(),
            usage_data={
                "aggregate": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }
            },
        )


class UnconfiguredVocabularyExecutor:
    async def generate(
        self,
        context: VocabularyJobContext,
    ) -> VocabularyExecutionResult:
        raise VocabularyExecutionError(
            (
                "vocabulary executor is not configured; inject an explicit fake "
                "executor for tests or wire a real executor for production"
            ),
            retryable=False,
            failure_class="configuration",
            failure_code="vocabulary_executor_unconfigured",
        )


# ---------------------------------------------------------------------------#
# T1.1 short-article batch path: batch compute, unit publish.
# ---------------------------------------------------------------------------#


@dataclass(frozen=True, slots=True)
class VocabularyBatchUnitContext:
    """Per-unit slice within a batch vocabulary context."""

    unit_id: str
    order_index: int
    source_text: str
    text_hash: str
    anchor_segments: tuple[VocabularyAnchorSegmentContext, ...]


@dataclass(frozen=True, slots=True)
class VocabularyBatchJobContext:
    """Batch vocabulary job context: covers all units of a short article."""

    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    base_id: UUID
    expected_generation: int
    operation_fingerprint: str
    source_language: str
    target_unit_ids: tuple[str, ...]
    units: tuple[VocabularyBatchUnitContext, ...]
    reading_goal: str
    reading_variant: str
    strategy_version: str
    strategy_hash: str
    layer_policy_hash: str
    vocabulary_prompt_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VocabularyBatchExecutionResult:
    output: VocabularyBatchCandidateOutput
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = FAKE_VOCABULARY_PROMPT_VERSION
    model_route: str = MODEL_ROUTE_READER_LAYER_VOCABULARY
    model_profile: str | None = FAKE_VOCABULARY_MODEL_PROFILE
    model_provider: str | None = FAKE_VOCABULARY_MODEL_PROVIDER
    model_name: str | None = FAKE_VOCABULARY_MODEL_NAME


@dataclass(frozen=True, slots=True)
class VocabularyBatchJobProcessResult:
    claim: ClaimResult
    context: VocabularyBatchJobContext | None
    status: str
    published_batch: PublishedVocabularyBatch | None = None
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


class VocabularyBatchExecutor(Protocol):
    async def generate_batch(
        self,
        context: VocabularyBatchJobContext,
    ) -> VocabularyBatchExecutionResult: ...


class PydanticAIVocabularyBatchExecutor:
    """Batch vocabulary executor: 1 LLM call covering all units."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    async def generate_batch(
        self,
        context: VocabularyBatchJobContext,
    ) -> VocabularyBatchExecutionResult:
        settings = self._settings or get_settings()
        prompt_version = get_prompt_version()
        if not str(settings.reader_vocabulary_model_profile or "").strip():
            raise VocabularyExecutionError(
                (
                    "vocabulary batch executor is not configured; set "
                    "reader_vocabulary_model_profile or inject an explicit fake "
                    "executor for tests"
                ),
                retryable=False,
                failure_class="configuration",
                failure_code="vocabulary_executor_unconfigured",
                prompt_version=prompt_version,
            )
        model, model_config = build_model_for_route(
            settings,
            MODEL_ROUTE_READER_LAYER_VOCABULARY,
        )
        if model is None:
            raise VocabularyExecutionError(
                "reader_layer_vocabulary model route is not configured",
                retryable=False,
                failure_class="configuration",
                failure_code="model_route_unavailable",
                prompt_version=prompt_version,
            )

        assert_real_llm_allowed(
            (
                "app.services.reader_orchestration.vocabulary_worker."
                "PydanticAIVocabularyBatchExecutor"
            ),
            model_config=model_config,
        )

        agent = Agent(
            model=model,
            output_type=VocabularyBatchCandidateOutput,
            instructions=load_agent_instructions(VOCABULARY_PROMPT_AGENT_NAME),
            name="reader_layer_vocabulary_batch_agent",
            retries={"tools": 1, "output": 2},
        )
        try:
            result = await agent.run(_build_vocabulary_batch_prompt(context))
        except Exception as exc:
            raise VocabularyExecutionError(
                f"reader_layer_vocabulary batch agent execution failed: {exc}",
                retryable=True,
                failure_class="provider",
                failure_code=type(exc).__name__,
                prompt_version=prompt_version,
                model_profile=(
                    str(model_config.profile_name) if model_config is not None else None
                ),
                model_provider=(
                    str(model_config.provider) if model_config is not None else None
                ),
                model_name=(
                    str(model_config.model_name) if model_config is not None else None
                ),
            ) from exc

        try:
            candidate_output = VocabularyBatchCandidateOutput.model_validate(
                result.output
            )
        except ValidationError as exc:
            raise VocabularyExecutionError(
                f"reader_layer_vocabulary batch produced invalid structured output: {exc}",
                retryable=False,
                failure_class="validation",
                failure_code="model_output_invalid",
                prompt_version=prompt_version,
                model_profile=(
                    str(model_config.profile_name) if model_config is not None else None
                ),
                model_provider=(
                    str(model_config.provider) if model_config is not None else None
                ),
                model_name=(
                    str(model_config.model_name) if model_config is not None else None
                ),
            ) from exc

        usage_data = extract_run_usage(result)
        return VocabularyBatchExecutionResult(
            output=candidate_output,
            usage_data=usage_data,
            prompt_version=prompt_version,
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


def _build_vocabulary_batch_prompt(context: VocabularyBatchJobContext) -> str:
    strategy_section = _format_vocabulary_batch_strategy_section(context)
    units_section = _format_vocabulary_batch_units_section(context)
    return (
        "Generate vocabulary highlights for the following reading units of a "
        "short article batch.\n"
        f"source_language: {context.source_language}\n"
        f"{strategy_section}"
        "Return only the structured VocabularyBatchCandidateOutput.\n"
        "Each unit must appear exactly once in units[] with its unit_id and "
        "the vocabulary candidate items for that unit.\n"
        f"{units_section}"
    )


def _format_vocabulary_batch_strategy_section(
    context: VocabularyBatchJobContext,
) -> str:
    if not context.vocabulary_prompt_lines:
        return ""
    rendered = "\n".join(context.vocabulary_prompt_lines)
    return f"<strategy>\n{rendered}\n</strategy>\n"


def _format_vocabulary_batch_units_section(
    context: VocabularyBatchJobContext,
) -> str:
    parts: list[str] = ["<units>"]
    for unit in context.units:
        parts.append(f'<unit unit_id="{unit.unit_id}">')
        parts.append("<source_text>")
        parts.append(unit.source_text)
        parts.append("</source_text>")
        parts.append("<target_segments>")
        for segment in unit.anchor_segments:
            parts.append(
                f'<segment anchor_segment_id="{segment.anchor_segment_id}" '
                f'sentence_id="{segment.sentence_id}" '
                f'segment_type="{segment.segment_type}" '
                f'boundary_quality="{segment.boundary_quality}" />'
            )
        parts.append("</target_segments>")
        parts.append("</unit>")
    parts.append("</units>")
    return "\n".join(parts) + "\n"


def _build_vocabulary_batch_outputs(
    *,
    context: VocabularyBatchJobContext,
    candidate_output: VocabularyBatchCandidateOutput,
) -> list[tuple[str, VocabularyLayerOutput]]:
    """Split a batch candidate output into per-unit ``VocabularyLayerOutput``.

    For each ``VocabularyBatchUnitCandidateOutput`` the function builds a
    temporary per-unit :class:`VocabularyJobContext` so the existing
    :func:`_build_vocabulary_output_from_candidates` can resolve anchors and
    produce the final :class:`VocabularyLayerOutput`.

    Fail-closed: any unit_id in the batch output that does not match a unit
    in the batch context raises :class:`VocabularyExecutionError`.
    """
    units_by_id = {unit.unit_id: unit for unit in context.units}
    outputs: list[tuple[str, VocabularyLayerOutput]] = []
    for batch_unit in candidate_output.units:
        unit_context = units_by_id.get(batch_unit.unit_id)
        if unit_context is None:
            raise VocabularyExecutionError(
                f"vocabulary batch output references unknown unit_id "
                f"{batch_unit.unit_id!r}",
                retryable=False,
                failure_class="validation",
                failure_code="vocabulary_batch_unknown_unit",
            )
        per_unit_context = VocabularyJobContext(
            job_id=context.job_id,
            run_id=context.run_id,
            reading_record_id=context.reading_record_id,
            user_id=context.user_id,
            base_id=context.base_id,
            unit_id=batch_unit.unit_id,
            order_index=unit_context.order_index,
            expected_generation=context.expected_generation,
            operation_fingerprint=context.operation_fingerprint,
            source_language=context.source_language,
            source_text=unit_context.source_text,
            text_hash=unit_context.text_hash,
            anchor_segments=unit_context.anchor_segments,
            reading_goal=context.reading_goal,
            reading_variant=context.reading_variant,
            strategy_version=context.strategy_version,
            strategy_hash=context.strategy_hash,
            layer_policy_hash=context.layer_policy_hash,
            vocabulary_prompt_lines=context.vocabulary_prompt_lines,
        )
        unit_candidate = VocabularyCandidateOutput(
            schema_version=candidate_output.schema_version,
            items=list(batch_unit.items),
        )
        output, _diagnostics = _build_vocabulary_output_from_candidates(
            per_unit_context,
            unit_candidate,
        )
        outputs.append((batch_unit.unit_id, output))
    return outputs


def _build_vocabulary_batch_quality_json(
    execution: VocabularyBatchExecutionResult,
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


class VocabularyWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        layer_publisher: VocabularyLayerPublisher | None = None,
        executor: VocabularyExecutor | None = None,
        batch_executor: VocabularyBatchExecutor | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._layer_publisher = layer_publisher or VocabularyLayerPublisher(pool=pool)
        self._executor = executor or PydanticAIVocabularyExecutor()
        self._batch_executor = batch_executor or PydanticAIVocabularyBatchExecutor()

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def claim_vocabulary_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> ClaimResult | None:
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=VOCABULARY_JOB_TYPE,
            target_type=VOCABULARY_TARGET_SCOPE,
            operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
        )
        if claim is None:
            return None
        if (
            claim.job_type != VOCABULARY_JOB_TYPE
            or claim.target_type != VOCABULARY_TARGET_SCOPE
            or not _fingerprint_matches_base(
                claim.operation_fingerprint, VOCABULARY_OPERATION_FINGERPRINT
            )
        ):
            raise RuntimeError(
                "vocabulary worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}/{claim.operation_fingerprint}"
            )
        await self._mark_run_running(claim.run_id)
        return claim

    async def claim_vocabulary_job_for_record(
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
            job_type=VOCABULARY_JOB_TYPE,
            target_type=VOCABULARY_TARGET_SCOPE,
            operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
            reading_record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if claim is None:
            return None
        if (
            claim.job_type != VOCABULARY_JOB_TYPE
            or claim.target_type != VOCABULARY_TARGET_SCOPE
            or not _fingerprint_matches_base(
                claim.operation_fingerprint, VOCABULARY_OPERATION_FINGERPRINT
            )
        ):
            raise RuntimeError(
                "vocabulary worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}/{claim.operation_fingerprint}"
            )
        await self._mark_run_running(claim.run_id)
        return claim

    async def heartbeat_vocabulary_job(
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

    async def process_next_vocabulary_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_VOCABULARY_RETRY_DELAY,
    ) -> VocabularyJobProcessResult | None:
        claim = await self.claim_vocabulary_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_vocabulary_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_next_vocabulary_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_VOCABULARY_RETRY_DELAY,
    ) -> VocabularyJobProcessResult | None:
        claim = await self.claim_vocabulary_job_for_record(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_vocabulary_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_claimed_vocabulary_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_VOCABULARY_RETRY_DELAY,
    ) -> VocabularyJobProcessResult:
        context: VocabularyJobContext | None = None

        try:
            context = await self._load_job_context(claim.job_id)
            execution = await self._executor.generate(context)
            output = VocabularyLayerOutput.model_validate(execution.output)
            published_layer = await self._layer_publisher.publish_unit_vocabulary(
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
                capability_code=CAPABILITY_READER_VOCABULARY,
            )
            return VocabularyJobProcessResult(
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
        except VocabularyExecutionError as exc:
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
                    prompt_version=exc.prompt_version,
                    model_route=exc.model_route,
                    model_profile=exc.model_profile,
                    model_provider=exc.model_provider,
                    model_name=exc.model_name,
                )
                await end_worker_span_execution_error(
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                )
                return VocabularyJobProcessResult(
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
                prompt_version=exc.prompt_version,
                model_route=exc.model_route,
                model_profile=exc.model_profile,
                model_provider=exc.model_provider,
                model_name=exc.model_name,
            )
            await end_worker_span_execution_error(
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
            )
            return VocabularyJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )
        except Exception as exc:
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="failed_terminal",
                lease_token=claim.lease_token,
                failure_class="vocabulary_execution",
                failure_code=type(exc).__name__,
                failure_message=str(exc),
                rationale_code="vocabulary_execution_failed",
            )
            await self._mark_run_status(
                claim.run_id,
                status="failed_terminal",
                failure_class="vocabulary_execution",
                failure_code=type(exc).__name__,
                finished_at=datetime.now(UTC),
            )
            await self._record_failed_usage_event(
                context=context,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            await end_worker_span_generic_exception(layer="vocabulary", exc=exc)
            return VocabularyJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )

    # ------------------------------------------------------------------#
    # T1.1 short-article batch path: claim / process / context loading.
    # ------------------------------------------------------------------#

    async def claim_vocabulary_batch_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> ClaimResult | None:
        """Claim a pending ``vocabulary_article`` batch job for the record."""
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=VOCABULARY_BATCH_JOB_TYPE,
            target_type=VOCABULARY_BATCH_TARGET_SCOPE,
            operation_fingerprint=VOCABULARY_BATCH_OPERATION_FINGERPRINT,
            reading_record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if claim is None:
            return None
        if (
            claim.job_type != VOCABULARY_BATCH_JOB_TYPE
            or claim.target_type != VOCABULARY_BATCH_TARGET_SCOPE
        ):
            raise RuntimeError(
                "vocabulary batch worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}"
            )
        await self._mark_run_running(claim.run_id)
        return claim

    async def process_next_vocabulary_batch_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_VOCABULARY_RETRY_DELAY,
    ) -> VocabularyBatchJobProcessResult | None:
        """Claim and process the next vocabulary batch job for the record."""
        claim = await self.claim_vocabulary_batch_job_for_record(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_vocabulary_batch_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_claimed_vocabulary_batch_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_VOCABULARY_RETRY_DELAY,
    ) -> VocabularyBatchJobProcessResult:
        """Run the batch LLM call and publish N per-unit vocabulary layers.

        Exception handling mirrors :meth:`process_claimed_vocabulary_job`:
        ``FenceViolationError`` → ``superseded``;
        ``VocabularyExecutionError`` (retryable) → ``retry_later``;
        ``VocabularyExecutionError`` (non-retryable) → ``failed_terminal``;
        any other ``Exception`` → ``failed_terminal``.
        """
        context: VocabularyBatchJobContext | None = None

        try:
            context = await self._load_batch_job_context(claim.job_id)
            execution = await self._batch_executor.generate_batch(context)
            outputs = _build_vocabulary_batch_outputs(
                context=context,
                candidate_output=execution.output,
            )
            published_batch = await self._layer_publisher.publish_article_vocabulary_batch(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                outputs=outputs,
                quality_json=_build_vocabulary_batch_quality_json(
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
                capability_code=CAPABILITY_READER_VOCABULARY,
            )
            return VocabularyBatchJobProcessResult(
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
        except VocabularyExecutionError as exc:
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
                    prompt_version=exc.prompt_version,
                    model_route=exc.model_route,
                    model_profile=exc.model_profile,
                    model_provider=exc.model_provider,
                    model_name=exc.model_name,
                )
                await end_worker_span_execution_error(
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                )
                return VocabularyBatchJobProcessResult(
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
                prompt_version=exc.prompt_version,
                model_route=exc.model_route,
                model_profile=exc.model_profile,
                model_provider=exc.model_provider,
                model_name=exc.model_name,
            )
            await end_worker_span_execution_error(
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
            )
            return VocabularyBatchJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )
        except Exception as exc:
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="failed_terminal",
                lease_token=claim.lease_token,
                failure_class="vocabulary_batch_execution",
                failure_code=type(exc).__name__,
                failure_message=str(exc),
                rationale_code="vocabulary_batch_execution_failed",
            )
            await self._mark_run_status(
                claim.run_id,
                status="failed_terminal",
                failure_class="vocabulary_batch_execution",
                failure_code=type(exc).__name__,
                finished_at=datetime.now(UTC),
            )
            await self._record_batch_failed_usage_event(
                context=context,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            await end_worker_span_generic_exception(layer="vocabulary", exc=exc)
            return VocabularyBatchJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )

    async def _load_batch_job_context(
        self,
        job_id: UUID,
    ) -> VocabularyBatchJobContext:
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
                       base.language AS source_language,
                       base.text AS base_text
                FROM reader_jobs job
                JOIN reading_bases base
                  ON base.id = job.base_id
                 AND base.reading_record_id = job.reading_record_id
                WHERE job.id = $1
                """,
                job_id,
            )
            if row is None:
                raise LookupError(f"reader job {job_id} not found")

            input_json = row["input_json"]
            target_unit_ids: list[str] = list(input_json.get("target_unit_ids") or [])
            if not target_unit_ids:
                raise VocabularyExecutionError(
                    f"vocabulary batch job {job_id} has no target_unit_ids",
                    retryable=False,
                    failure_class="validation",
                    failure_code="vocabulary_batch_empty_target_units",
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
                raise VocabularyExecutionError(
                    f"vocabulary batch job {job_id} references missing units "
                    f"{sorted(missing)!r}",
                    retryable=False,
                    failure_class="validation",
                    failure_code="vocabulary_batch_missing_unit",
                )

            units: list[VocabularyBatchUnitContext] = []
            for unit_row in unit_rows:
                unit_id = str(unit_row["unit_id"])
                source_text = slice_by_utf16_offsets(
                    base_text,
                    int(unit_row["base_start_utf16"]),
                    int(unit_row["base_end_utf16"]),
                )
                if source_text is None or not source_text:
                    raise VocabularyExecutionError(
                        f"vocabulary batch unit {unit_id} could not be sliced from base text",
                        retryable=False,
                        failure_class="validation",
                        failure_code="unit_slice_failed",
                    )
                actual_hash = compute_text_range_hash(source_text)
                expected_hash = str(unit_row["text_hash"])
                if actual_hash != expected_hash:
                    raise VocabularyExecutionError(
                        f"vocabulary batch unit {unit_id} hash mismatch: "
                        f"{actual_hash} != {expected_hash}",
                        retryable=False,
                        failure_class="validation",
                        failure_code="unit_hash_mismatch",
                    )

                segment_rows = await conn.fetch(
                    """
                    SELECT anchor_segment_id,
                           sentence_id,
                           segment_type,
                           unit_start_utf16,
                           unit_end_utf16,
                           text_hash,
                           boundary_quality
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
                anchor_segments: list[VocabularyAnchorSegmentContext] = []
                for segment_row in segment_rows:
                    segment_text = slice_by_utf16_offsets(
                        source_text,
                        int(segment_row["unit_start_utf16"]),
                        int(segment_row["unit_end_utf16"]),
                    )
                    if segment_text is None or not segment_text:
                        raise VocabularyExecutionError(
                            f"vocabulary batch anchor segment "
                            f"{segment_row['anchor_segment_id']} could not be sliced",
                            retryable=False,
                            failure_class="validation",
                            failure_code="anchor_segment_slice_failed",
                        )
                    segment_hash = str(segment_row["text_hash"])
                    if compute_text_range_hash(segment_text) != segment_hash:
                        raise VocabularyExecutionError(
                            f"vocabulary batch anchor segment "
                            f"{segment_row['anchor_segment_id']} hash mismatch",
                            retryable=False,
                            failure_class="validation",
                            failure_code="anchor_segment_hash_mismatch",
                        )
                    anchor_segments.append(
                        VocabularyAnchorSegmentContext(
                            anchor_segment_id=str(segment_row["anchor_segment_id"]),
                            sentence_id=str(
                                segment_row["sentence_id"]
                                or segment_row["anchor_segment_id"]
                            ),
                            segment_type=str(segment_row["segment_type"]),
                            unit_start_utf16=int(segment_row["unit_start_utf16"]),
                            unit_end_utf16=int(segment_row["unit_end_utf16"]),
                            text_hash=segment_hash,
                            text=segment_text,
                            boundary_quality=str(
                                segment_row.get("boundary_quality") or "normal"
                            ),
                        )
                    )
                if not anchor_segments:
                    raise VocabularyExecutionError(
                        f"vocabulary batch unit {unit_id} has no anchor segments",
                        retryable=False,
                        failure_class="validation",
                        failure_code="missing_anchor_segments",
                    )
                units.append(
                    VocabularyBatchUnitContext(
                        unit_id=unit_id,
                        order_index=int(unit_row["order_index"]),
                        source_text=source_text,
                        text_hash=expected_hash,
                        anchor_segments=tuple(anchor_segments),
                    )
                )

            strategy_metadata = _validate_vocabulary_strategy_metadata(input_json)

            return VocabularyBatchJobContext(
                job_id=row["id"],
                run_id=row["run_id"],
                reading_record_id=row["reading_record_id"],
                user_id=row["user_id"],
                base_id=row["base_id"],
                expected_generation=int(row["expected_generation"]),
                operation_fingerprint=str(row["operation_fingerprint"]),
                source_language=str(row["source_language"] or "en"),
                target_unit_ids=tuple(target_unit_ids),
                units=tuple(units),
                reading_goal=strategy_metadata.reading_goal,
                reading_variant=strategy_metadata.reading_variant,
                strategy_version=strategy_metadata.strategy_version,
                strategy_hash=strategy_metadata.strategy_hash,
                layer_policy_hash=strategy_metadata.layer_policy_hash,
                vocabulary_prompt_lines=strategy_metadata.vocabulary_prompt_lines,
            )

    async def _record_batch_usage_event(
        self,
        *,
        context: VocabularyBatchJobContext,
        execution: VocabularyBatchExecutionResult,
        published_batch: PublishedVocabularyBatch,
        status: str,
    ) -> UUID | None:
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_VOCABULARY,
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
                workflow_version="t1-1-vocabulary-batch-worker",
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
                    "source_language": context.source_language,
                    "batch": True,
                },
            )
        )

    async def _record_batch_failed_usage_event(
        self,
        *,
        context: VocabularyBatchJobContext | None,
        error_code: str,
        error_message: str,
        prompt_version: str | None = None,
        model_route: str = MODEL_ROUTE_READER_LAYER_VOCABULARY,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> UUID | None:
        if context is None:
            return None
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_VOCABULARY,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_FAILED,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version="t1-1-vocabulary-batch-worker",
                prompt_version=prompt_version,
                model_route=model_route,
                model_profile_id=model_profile,
                model_profile=model_profile,
                model_provider=model_provider,
                model_name=model_name,
                planner_kind="llm_worker",
                operation_fingerprint=context.operation_fingerprint,
                error_code=error_code,
                error_message=error_message,
                metadata_json={
                    "base_id": str(context.base_id),
                    "target_unit_ids": list(context.target_unit_ids),
                    "unit_count": len(context.units),
                    "source_language": context.source_language,
                    "batch": True,
                },
            )
        )

    async def _load_job_context(self, job_id: UUID) -> VocabularyJobContext:
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
                raise VocabularyExecutionError(
                    f"vocabulary unit {row['target_key']} could not be sliced from base text",
                    retryable=False,
                    failure_class="validation",
                    failure_code="unit_slice_failed",
                )
            expected_hash = str(row["text_hash"])
            actual_hash = compute_text_range_hash(source_text)
            if actual_hash != expected_hash:
                raise VocabularyExecutionError(
                    (
                        f"vocabulary unit {row['target_key']} hash mismatch: "
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
                       segment_type,
                       unit_start_utf16,
                       unit_end_utf16,
                       text_hash,
                       boundary_quality
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

        anchor_segments: list[VocabularyAnchorSegmentContext] = []
        for segment_row in segment_rows:
            segment_text = slice_by_utf16_offsets(
                source_text,
                int(segment_row["unit_start_utf16"]),
                int(segment_row["unit_end_utf16"]),
            )
            if segment_text is None or not segment_text:
                raise VocabularyExecutionError(
                    (
                        f"vocabulary anchor segment {segment_row['anchor_segment_id']} "
                        "could not be sliced from unit text"
                    ),
                    retryable=False,
                    failure_class="validation",
                    failure_code="anchor_segment_slice_failed",
                )
            segment_hash = str(segment_row["text_hash"])
            if compute_text_range_hash(segment_text) != segment_hash:
                raise VocabularyExecutionError(
                    (
                        f"vocabulary anchor segment {segment_row['anchor_segment_id']} "
                        "hash mismatch"
                    ),
                    retryable=False,
                    failure_class="validation",
                    failure_code="anchor_segment_hash_mismatch",
                )
            anchor_segments.append(
                VocabularyAnchorSegmentContext(
                    anchor_segment_id=str(segment_row["anchor_segment_id"]),
                    sentence_id=str(
                        segment_row["sentence_id"] or segment_row["anchor_segment_id"]
                    ),
                    segment_type=str(segment_row["segment_type"]),
                    unit_start_utf16=int(segment_row["unit_start_utf16"]),
                    unit_end_utf16=int(segment_row["unit_end_utf16"]),
                    text_hash=segment_hash,
                    text=segment_text,
                    boundary_quality=str(segment_row.get("boundary_quality") or "normal"),
                )
            )

        if not anchor_segments:
            raise VocabularyExecutionError(
                f"vocabulary unit {row['target_key']} has no anchor segments",
                retryable=False,
                failure_class="validation",
                failure_code="missing_anchor_segments",
            )

        # T7: read strategy metadata written by T5 bootstrap from
        # input_json and cross-validate against the live resolver. Missing
        # metadata or hash mismatch fail closed; legacy bare-fingerprint
        # jobs without strategy metadata are rejected, never silently
        # downgraded to a default strategy.
        input_json = row["input_json"]
        strategy_metadata = _validate_vocabulary_strategy_metadata(input_json)

        return VocabularyJobContext(
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
            source_text=source_text,
            text_hash=expected_hash,
            anchor_segments=tuple(anchor_segments),
            reading_goal=strategy_metadata.reading_goal,
            reading_variant=strategy_metadata.reading_variant,
            strategy_version=strategy_metadata.strategy_version,
            strategy_hash=strategy_metadata.strategy_hash,
            layer_policy_hash=strategy_metadata.layer_policy_hash,
            vocabulary_prompt_lines=strategy_metadata.vocabulary_prompt_lines,
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
        context: VocabularyJobContext,
        execution: VocabularyExecutionResult,
        published_layer: PublishedVocabularyLayer,
        status: str,
    ) -> UUID | None:
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_VOCABULARY,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=status,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                enhancement_layer_id=published_layer.layer_id,
                workflow_name="reader_orchestration",
                workflow_version=VOCABULARY_WORKFLOW_VERSION,
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
                    "source_language": context.source_language,
                    "anchor_segment_count": len(context.anchor_segments),
                },
            )
        )

    async def _record_failed_usage_event(
        self,
        *,
        context: VocabularyJobContext | None,
        error_code: str,
        error_message: str,
        prompt_version: str | None = None,
        model_route: str = MODEL_ROUTE_READER_LAYER_VOCABULARY,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> UUID | None:
        if context is None:
            return None
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_VOCABULARY,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_FAILED,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version=VOCABULARY_WORKFLOW_VERSION,
                prompt_version=prompt_version,
                model_route=model_route,
                model_profile_id=model_profile,
                model_profile=model_profile,
                model_provider=model_provider,
                model_name=model_name,
                planner_kind="llm_worker",
                operation_fingerprint=context.operation_fingerprint,
                error_code=error_code,
                error_message=error_message,
                metadata_json={
                    "base_id": str(context.base_id),
                    "unit_id": context.unit_id,
                    "source_language": context.source_language,
                    "anchor_segment_count": len(context.anchor_segments),
                },
            )
        )


def _build_vocabulary_prompt(context: VocabularyJobContext) -> str:
    strategy_section = _format_vocabulary_strategy_section(context)
    anchor_segments = [
        {
            "anchor_segment_id": segment.anchor_segment_id,
            "sentence_id": segment.sentence_id,
            "segment_type": segment.segment_type,
            "unit_start_utf16": segment.unit_start_utf16,
            "unit_end_utf16": segment.unit_end_utf16,
            "text": segment.text,
        }
        for segment in context.anchor_segments
    ]
    return (
        "Generate high-value vocabulary annotations for a single reading unit.\n"
        f"source_language: {context.source_language}\n"
        f"unit_id: {context.unit_id}\n"
        f"max_items: {MAX_VOCABULARY_ITEMS}\n"
        f"{strategy_section}"
        "Return only the structured candidate output.\n"
        "<source_text>\n"
        f"{context.source_text}\n"
        "</source_text>\n"
        "<anchor_segments_json>\n"
        f"{json.dumps(anchor_segments, ensure_ascii=False)}\n"
        "</anchor_segments_json>"
    )


def _format_vocabulary_strategy_section(context: VocabularyJobContext) -> str:
    """Format the concrete vocabulary policy lines as a prompt section.

    The strategy section carries the resolved variant-first policy lines
    (from ``reader_variants.yaml`` via ``resolve_reader_variant_strategy``)
    so the vocabulary agent can vary its annotation choices by
    ``reading_goal`` / ``reading_variant``. The accompanying hashes are
    included for traceability and so that prompt-level evals can group by
    strategy.
    """
    lines_bullet = "\n".join(
        f"- {line}" for line in context.vocabulary_prompt_lines
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
class _VocabularyStrategyMetadata:
    """Validated strategy metadata extracted from a vocabulary job's
    input_json and cross-checked against the live resolver."""

    reading_goal: str
    reading_variant: str
    strategy_version: str
    strategy_hash: str
    layer_policy_hash: str
    vocabulary_prompt_lines: tuple[str, ...]


def _validate_vocabulary_strategy_metadata(
    input_json: Any,
) -> _VocabularyStrategyMetadata:
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
          :class:`VocabularyExecutionError` with failure_class
          ``strategy_resolution``.
        - ``strategy_version``, ``strategy_hash`` and
          ``layer_policy_hash`` from input_json must match the resolver
          output exactly. Any mismatch fails closed with a dedicated
          failure_code.
    """
    if not isinstance(input_json, Mapping):
        raise VocabularyExecutionError(
            "vocabulary job input_json is not a mapping; "
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
        raise VocabularyExecutionError(
            "vocabulary job input_json is missing strategy metadata: "
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
        raise VocabularyExecutionError(
            f"vocabulary strategy resolver rejected pair "
            f"({reading_goal!r}, {reading_variant!r}): {exc}",
            retryable=False,
            failure_class="strategy_resolution",
            failure_code="strategy_resolver_error",
        ) from exc

    if strategy.strategy_version != expected_strategy_version:
        raise VocabularyExecutionError(
            f"vocabulary strategy_version mismatch: input_json has "
            f"{expected_strategy_version!r} but resolver produced "
            f"{strategy.strategy_version!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_STRATEGY_VERSION_MISMATCH_CODE,
        )

    if strategy.strategy_hash != expected_strategy_hash:
        raise VocabularyExecutionError(
            f"vocabulary strategy_hash mismatch: input_json has "
            f"{expected_strategy_hash!r} but resolver produced "
            f"{strategy.strategy_hash!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_STRATEGY_HASH_MISMATCH_CODE,
        )

    layer = strategy.layers.get(_VOCABULARY_LAYER_NAME)
    if layer is None:
        # Defensive: the resolver guarantees all REQUIRED_LAYERS are
        # present. Fail closed if a future code path violates that.
        raise VocabularyExecutionError(
            f"resolved strategy has no layer {_VOCABULARY_LAYER_NAME!r}",
            retryable=False,
            failure_class="strategy_resolution",
            failure_code="strategy_resolver_error",
        )

    if layer.policy_hash != expected_layer_policy_hash:
        raise VocabularyExecutionError(
            f"vocabulary layer_policy_hash mismatch: input_json has "
            f"{expected_layer_policy_hash!r} but resolver produced "
            f"{layer.policy_hash!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_LAYER_POLICY_HASH_MISMATCH_CODE,
        )

    return _VocabularyStrategyMetadata(
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        vocabulary_prompt_lines=layer.prompt_lines,
    )


def _build_vocabulary_output_from_candidates(
    context: VocabularyJobContext,
    candidate_output: VocabularyCandidateOutput,
) -> tuple[VocabularyLayerOutput, dict[str, Any]]:
    segments_by_id = {
        segment.anchor_segment_id: segment
        for segment in context.anchor_segments
    }
    skipped_items: list[dict[str, Any]] = []
    resolved_spans: dict[tuple[str, int, int], _ResolvedVocabularyCandidate] = {}

    for item_index, item in enumerate(candidate_output.items):
        segment = segments_by_id.get(item.anchor_segment_id)
        if segment is None:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code="anchor_segment_unknown",
                )
            )
            continue

        if segment.segment_type == "fallback_window":
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code="boundary_low_fallback_window",
                )
            )
            continue

        occurrences = _find_unique_segment_occurrences(segment.text, item.selected_text)
        if not occurrences:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code="selected_text_not_found",
                )
            )
            continue
        if len(occurrences) > 1:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code="selected_text_ambiguous",
                )
            )
            continue

        segment_start, segment_end = occurrences[0]
        start_offset = segment.unit_start_utf16 + segment_start
        end_offset = segment.unit_start_utf16 + segment_end
        if start_offset < segment.unit_start_utf16 or end_offset > segment.unit_end_utf16:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code="selected_text_outside_segment",
                )
            )
            continue

        resolved_text = slice_by_utf16_offsets(
            context.source_text,
            start_offset,
            end_offset,
        )
        if resolved_text != item.selected_text:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code="selected_text_slice_mismatch",
                )
            )
            continue

        span_key = (
            item.anchor_segment_id,
            start_offset,
            end_offset,
        )
        item_priority = _vocabulary_item_priority(item.item_type)
        existing_resolution = resolved_spans.get(span_key)
        if existing_resolution is not None:
            if item_priority < existing_resolution.priority:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=existing_resolution.item_index,
                        item_type=existing_resolution.item_type,
                        anchor_segment_id=existing_resolution.anchor_segment_id,
                        selected_text=existing_resolution.selected_text,
                        reason_code="span_conflict_higher_priority_kept",
                    )
                )
            else:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=item_index,
                        item_type=item.item_type,
                        anchor_segment_id=item.anchor_segment_id,
                        selected_text=item.selected_text,
                        reason_code="span_conflict_higher_priority_kept",
                    )
                )
                continue

        elif len(resolved_spans) >= MAX_VOCABULARY_ITEMS:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code="candidate_limit_exceeded",
                )
            )
            continue

        anchor = ReaderTextRangeAnchor(
            base_id=str(context.base_id),
            unit_id=context.unit_id,
            anchor_segment_id=segment.anchor_segment_id,
            sentence_id=segment.sentence_id,
            segment_type=segment.segment_type,  # type: ignore[arg-type]
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=resolved_text,
            text_hash=compute_text_range_hash(resolved_text),
        )

        try:
            resolved_item = _build_resolved_vocabulary_item(item, anchor)
        except ValidationError:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code="resolved_item_invalid",
                )
            )
            continue

        if existing_resolution is not None and item_priority < existing_resolution.priority:
            resolved_spans[span_key] = _ResolvedVocabularyCandidate(
                order_index=existing_resolution.order_index,
                item_index=item_index,
                item_type=item.item_type,
                anchor_segment_id=item.anchor_segment_id,
                selected_text=item.selected_text,
                priority=item_priority,
                resolved_item=resolved_item,
            )
            continue

        if existing_resolution is not None:
            continue

        resolved_spans[span_key] = _ResolvedVocabularyCandidate(
            order_index=item_index,
            item_index=item_index,
            item_type=item.item_type,
            anchor_segment_id=item.anchor_segment_id,
            selected_text=item.selected_text,
            priority=item_priority,
            resolved_item=resolved_item,
        )

    resolved_items = [
        candidate.resolved_item
        for candidate in sorted(
            resolved_spans.values(),
            key=lambda candidate: candidate.order_index,
        )
    ]

    trimmed_skipped_items = _trim_skipped_diagnostics(skipped_items)
    return VocabularyLayerOutput(items=resolved_items), {
        "candidate_item_count": len(candidate_output.items),
        "resolved_item_count": len(resolved_items),
        "skipped_item_count": len(skipped_items),
        "skipped_items": trimmed_skipped_items,
        "skipped_items_truncated_count": max(
            0,
            len(skipped_items) - len(trimmed_skipped_items),
        ),
    }


def _build_resolved_vocabulary_item(
    item: VocabularyCandidateItem,
    anchor: ReaderTextRangeAnchor,
) -> VocabularyHighlightItem | VocabularyPhraseGlossItem | VocabularyContextGlossItem:
    if item.item_type == "vocab_highlight":
        return VocabularyHighlightItem(
            anchor=anchor,
            headword=item.headword,
            brief_explanation=item.brief_explanation,
            reason=item.reason,
        )
    if item.item_type == "phrase_gloss":
        return VocabularyPhraseGlossItem(
            anchor=anchor,
            phrase=item.phrase,
            phrase_type=item.phrase_type,
            gloss=item.gloss,
            example=item.example,
        )
    return VocabularyContextGlossItem(
        anchor=anchor,
        display=item.display,
        gloss=item.gloss,
        reason=item.reason,
    )


def _find_unique_segment_occurrences(
    segment_text: str,
    selected_text: str,
) -> list[tuple[int, int]]:
    occurrences: list[tuple[int, int]] = []
    search_start = 0
    selected_text_length = len(selected_text)
    while True:
        index = segment_text.find(selected_text, search_start)
        if index < 0:
            break
        start_offset = len(
            segment_text[:index].encode("utf-16-le", "surrogatepass")
        ) // 2
        end_offset = start_offset + len(
            selected_text.encode("utf-16-le", "surrogatepass")
        ) // 2
        occurrences.append((start_offset, end_offset))
        search_start = index + max(1, selected_text_length)
    return occurrences


def _vocabulary_item_priority(item_type: str) -> int:
    priority = {
        "context_gloss": 0,
        "phrase_gloss": 1,
        "vocab_highlight": 2,
    }
    return priority.get(item_type, 99)


def _build_skip_diagnostic(
    *,
    item_index: int,
    item_type: str,
    anchor_segment_id: str,
    selected_text: str,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "item_index": item_index,
        "item_type": item_type,
        "anchor_segment_id": anchor_segment_id,
        "selected_text": _truncate_diagnostic_text(selected_text),
        "reason_code": reason_code,
    }


def _trim_skipped_diagnostics(skipped_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return skipped_items[:MAX_VOCABULARY_DIAGNOSTIC_ITEMS]


def _truncate_diagnostic_text(text: str) -> str:
    if len(text) <= MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH:
        return text
    return text[: MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH - 3] + "..."


def _build_quality_json(
    output: VocabularyLayerOutput,
    execution: VocabularyExecutionResult,
) -> dict[str, Any]:
    quality_json: dict[str, Any] = {
        "item_count": len(output.items),
        "item_types": [item.item_type for item in output.items],
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
    if execution.diagnostics is not None:
        quality_json["diagnostics"] = execution.diagnostics
    return quality_json
