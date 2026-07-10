from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
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
from app.llm.routes import MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    GrammarNoteItem,
    GrammarNoteLayerOutput,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
    SentenceAnalysisItem,
    SentenceAnalysisLayerOutput,
)
from app.services.ai_usage import (
    BILLING_MODE_INTERNAL_ONLY,
    CAPABILITY_READER_GRAMMAR_BUNDLE,
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
    GRAMMAR_BATCH_JOB_TYPE,
    GRAMMAR_BATCH_OPERATION_FINGERPRINT,
    GRAMMAR_BATCH_POLICY_VERSION,
    GRAMMAR_BATCH_TARGET_SCOPE,
    GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT,
    GRAMMAR_JOB_TYPE,
    GRAMMAR_OPERATION_FINGERPRINT,
    GRAMMAR_TARGET_SCOPE,
    _fingerprint_matches_base,
)
from .job_runtime import ClaimResult, FenceViolationError, ReaderJobRuntime
from .layer_publisher import (
    GrammarBundleLayerPublisher,
    PublishedGrammarBatch,
    PublishedGrammarBundle,
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

DEFAULT_GRAMMAR_RETRY_DELAY = timedelta(minutes=5)
GRAMMAR_WORKFLOW_VERSION = "d5-v6-grammar-worker"
GRAMMAR_PROMPT_AGENT_NAME = "reader_layer_grammar_bundle"
GRAMMAR_MODEL_ROUTE = MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE
FAKE_GRAMMAR_PROMPT_VERSION = "fake-grammar-worker-v1"
FAKE_GRAMMAR_MODEL_PROFILE = "fake-reader-layer-grammar-bundle"
FAKE_GRAMMAR_MODEL_PROVIDER = "fake-provider"
FAKE_GRAMMAR_MODEL_NAME = "fake-grammar-model"
MAX_GRAMMAR_NOTE_ITEMS = 4
MAX_SENTENCE_ANALYSIS_ITEMS = 3
MAX_GRAMMAR_SPANS_PER_NOTE = 4
MAX_GRAMMAR_SPAN_TEXT_LENGTH = 240
MAX_SENTENCE_ANALYSIS_TEXT_LENGTH = 640
MAX_GRAMMAR_LABEL_LENGTH = 120
MAX_GRAMMAR_FIELD_LENGTH = 360
MAX_GRAMMAR_CHUNKS_PER_ANALYSIS = 8
MAX_GRAMMAR_DIAGNOSTIC_ITEMS = 8
MAX_GRAMMAR_DIAGNOSTIC_TEXT_LENGTH = 80

# T8: variant-first strategy metadata keys read from reader_jobs.input_json.
# Must match the keys written by _build_strategy_metadata in job_bootstrap.
_STRATEGY_INPUT_KEYS: tuple[str, ...] = (
    "reading_goal",
    "reading_variant",
    "strategy_version",
    "strategy_hash",
    "layer_policy_hash",
)
_GRAMMAR_LAYER_NAME = "grammar_bundle"
_STRATEGY_METADATA_MISSING_CODE = "strategy_metadata_missing"
_STRATEGY_HASH_MISMATCH_CODE = "strategy_hash_mismatch"
_LAYER_POLICY_HASH_MISMATCH_CODE = "layer_policy_hash_mismatch"
_STRATEGY_VERSION_MISMATCH_CODE = "strategy_version_mismatch"


@dataclass(frozen=True, slots=True)
class GrammarAnchorSegmentContext:
    anchor_segment_id: str
    sentence_id: str
    segment_type: str
    unit_start_utf16: int
    unit_end_utf16: int
    text_hash: str
    text: str


@dataclass(frozen=True, slots=True)
class GrammarJobContext:
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
    anchor_segments: tuple[GrammarAnchorSegmentContext, ...]
    reading_goal: str
    reading_variant: str
    strategy_version: str
    strategy_hash: str
    layer_policy_hash: str
    grammar_prompt_lines: tuple[str, ...]


class GrammarCandidateSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_segment_id: str = Field(min_length=1)
    selected_text: str = Field(min_length=1, max_length=MAX_GRAMMAR_SPAN_TEXT_LENGTH)


class GrammarNoteCandidateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["grammar_note"] = "grammar_note"
    spans: list[GrammarCandidateSpan] = Field(
        min_length=1,
        max_length=MAX_GRAMMAR_SPANS_PER_NOTE,
    )
    grammar_point: str = Field(min_length=1, max_length=MAX_GRAMMAR_LABEL_LENGTH)
    pattern: str | None = Field(default=None, max_length=MAX_GRAMMAR_LABEL_LENGTH)
    note: str = Field(
        min_length=1,
        max_length=MAX_GRAMMAR_FIELD_LENGTH,
        description=(
            "简体中文 Markdown string。允许 **加粗**、`inline code`、短无序列表；"
            "禁止 raw HTML 和 Markdown 标题（# / ## / ###）。前端会把 Markdown "
            "反序列化为 Plate children 渲染。"
        ),
    )


class SentenceAnalysisChunkCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=MAX_GRAMMAR_LABEL_LENGTH)
    text: str = Field(min_length=1, max_length=MAX_GRAMMAR_SPAN_TEXT_LENGTH)


class SentenceAnalysisCandidateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["sentence_analysis"] = "sentence_analysis"
    anchor_segment_id: str = Field(min_length=1)
    selected_text: str = Field(
        min_length=1,
        max_length=MAX_SENTENCE_ANALYSIS_TEXT_LENGTH,
    )
    label: str = Field(min_length=1, max_length=MAX_GRAMMAR_LABEL_LENGTH)
    analysis: str = Field(
        min_length=1,
        max_length=MAX_GRAMMAR_FIELD_LENGTH,
        description=(
            "简体中文 Markdown string。允许 **加粗**、`inline code`、短无序列表；"
            "禁止 raw HTML 和 Markdown 标题（# / ## / ###）。讲解结构关系和阅读"
            "顺序，不要逐块复述 chunks。前端会把 Markdown 反序列化为 Plate "
            "children 渲染。"
        ),
    )
    chunks: list[SentenceAnalysisChunkCandidate] = Field(
        min_length=1,
        max_length=MAX_GRAMMAR_CHUNKS_PER_ANALYSIS,
    )


class GrammarBundleCandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    grammar_notes: list[GrammarNoteCandidateItem] = Field(
        default_factory=list,
        max_length=MAX_GRAMMAR_NOTE_ITEMS,
    )
    sentence_analyses: list[SentenceAnalysisCandidateItem] = Field(
        default_factory=list,
        max_length=MAX_SENTENCE_ANALYSIS_ITEMS,
    )


# T4.1c: batch candidate output covers ALL units in one LLM call.
# No fixed max_length on the lists — per-unit budget is enforced after
# splitting by unit_id (MAX_GRAMMAR_NOTE_ITEMS / MAX_SENTENCE_ANALYSIS_ITEMS
# per unit).
class GrammarBatchCandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    grammar_notes: list[GrammarNoteCandidateItem] = Field(
        default_factory=list,
    )
    sentence_analyses: list[SentenceAnalysisCandidateItem] = Field(
        default_factory=list,
    )


@dataclass(frozen=True, slots=True)
class GrammarExecutionResult:
    output: GrammarBundleOutput
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = FAKE_GRAMMAR_PROMPT_VERSION
    model_route: str = GRAMMAR_MODEL_ROUTE
    model_profile: str | None = FAKE_GRAMMAR_MODEL_PROFILE
    model_provider: str | None = FAKE_GRAMMAR_MODEL_PROVIDER
    model_name: str | None = FAKE_GRAMMAR_MODEL_NAME
    diagnostics: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class GrammarJobProcessResult:
    claim: ClaimResult
    context: GrammarJobContext | None
    status: str
    output: GrammarBundleOutput | None = None
    published_bundle: PublishedGrammarBundle | None = None
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


class GrammarExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_class: str,
        failure_code: str,
        rationale_code: str | None = None,
        prompt_version: str | None = None,
        model_route: str = GRAMMAR_MODEL_ROUTE,
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


class GrammarBundleExecutor(Protocol):
    async def generate(
        self,
        context: GrammarJobContext,
    ) -> GrammarExecutionResult: ...


class PydanticAIGrammarBundleExecutor:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _build_agent(self, *, model: Any) -> Agent:
        return Agent(
            model=model,
            output_type=GrammarBundleCandidateOutput,
            instructions=load_agent_instructions(GRAMMAR_PROMPT_AGENT_NAME),
            name="reader_layer_grammar_bundle_agent",
            retries={"tools": 1, "output": 2},
        )

    async def _run_agent(self, agent: Agent, prompt: str) -> Any:
        return await agent.run(prompt)

    async def generate(
        self,
        context: GrammarJobContext,
    ) -> GrammarExecutionResult:
        settings = self._settings or get_settings()
        prompt_version = get_prompt_version()
        if not str(settings.reader_grammar_bundle_model_profile or "").strip():
            raise GrammarExecutionError(
                (
                    "grammar bundle executor is not configured; set "
                    "reader_grammar_bundle_model_profile or inject an explicit fake "
                    "executor for tests"
                ),
                retryable=False,
                failure_class="configuration",
                failure_code="grammar_bundle_executor_unconfigured",
                prompt_version=prompt_version,
            )
        model, model_config = build_model_for_route(
            settings,
            MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE,
        )
        if model is None:
            raise GrammarExecutionError(
                "reader_layer_grammar_bundle model route is not configured",
                retryable=False,
                failure_class="configuration",
                failure_code="model_route_unavailable",
                prompt_version=prompt_version,
            )

        assert_real_llm_allowed(
            (
                "app.services.reader_orchestration.grammar_worker."
                "PydanticAIGrammarBundleExecutor"
            ),
            model_config=model_config,
        )

        agent = self._build_agent(model=model)
        try:
            result = await self._run_agent(agent, _build_grammar_prompt(context))
        except GrammarExecutionError:
            raise
        except Exception as exc:
            raise GrammarExecutionError(
                f"reader_layer_grammar_bundle agent execution failed: {exc}",
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
            candidate_output = GrammarBundleCandidateOutput.model_validate(result.output)
        except ValidationError as exc:
            raise GrammarExecutionError(
                f"reader_layer_grammar_bundle produced invalid structured output: {exc}",
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

        output, diagnostics = _build_grammar_output_from_candidates(
            context,
            candidate_output,
        )
        usage_data = extract_run_usage(result)
        return GrammarExecutionResult(
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


class FakeGrammarBundleExecutor:
    async def generate(
        self,
        context: GrammarJobContext,
    ) -> GrammarExecutionResult:
        return GrammarExecutionResult(
            output=GrammarBundleOutput(),
            usage_data={
                "aggregate": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }
            },
        )


class UnconfiguredGrammarBundleExecutor:
    async def generate(
        self,
        context: GrammarJobContext,
    ) -> GrammarExecutionResult:
        raise GrammarExecutionError(
            (
                "grammar bundle executor is not configured; inject an explicit fake "
                "executor for tests or wire a real executor for production"
            ),
            retryable=False,
            failure_class="configuration",
            failure_code="grammar_bundle_executor_unconfigured",
        )


# ---------------------------------------------------------------------------#
# T4.1c: compact grammar batch path (SHORT_BATCH / STRUCTURED_BATCH)
# ---------------------------------------------------------------------------#
#
# One ``build_grammar_bundle`` / ``unit_range`` batch job covers all unpublished
# units in a single LLM call. The batch executor builds a prompt that
# includes every unit's source text + anchor segments, asks the model to
# generate grammar_note / sentence_analysis candidates across all units,
# then splits the output by ``unit_id`` (each candidate references an
# ``anchor_segment_id`` which maps to exactly one unit). Per-unit budget
# (MAX_GRAMMAR_NOTE_ITEMS / MAX_SENTENCE_ANALYSIS_ITEMS) is enforced
# after splitting. The batch publisher writes per-unit
# ``enhancement_layers`` rows in one transaction, preserving the same
# grounded / anchor / budget contract as the legacy per-unit path.


@dataclass(frozen=True, slots=True)
class GrammarBatchUnitContext:
    """Per-unit context within a compact grammar batch job."""

    unit_id: str
    order_index: int
    source_text: str
    text_hash: str
    anchor_segments: tuple[GrammarAnchorSegmentContext, ...]


@dataclass(frozen=True, slots=True)
class GrammarBatchJobContext:
    """Batch grammar context covering all target units in one LLM call."""

    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    base_id: UUID
    expected_generation: int
    operation_fingerprint: str
    source_language: str
    units: tuple[GrammarBatchUnitContext, ...]
    reading_goal: str
    reading_variant: str
    strategy_version: str
    strategy_hash: str
    layer_policy_hash: str
    grammar_prompt_lines: tuple[str, ...]
    article_route: str
    document_features: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class GrammarBatchExecutionResult:
    """Per-unit outputs from a single batch LLM call."""

    outputs: list[tuple[str, GrammarBundleOutput]]
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = FAKE_GRAMMAR_PROMPT_VERSION
    model_route: str = GRAMMAR_MODEL_ROUTE
    model_profile: str | None = FAKE_GRAMMAR_MODEL_PROFILE
    model_provider: str | None = FAKE_GRAMMAR_MODEL_PROVIDER
    model_name: str | None = FAKE_GRAMMAR_MODEL_NAME
    diagnostics: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class GrammarBatchJobProcessResult:
    claim: ClaimResult
    context: GrammarBatchJobContext | None
    status: str
    published_batch: PublishedGrammarBatch | None = None
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


class GrammarBatchExecutor(Protocol):
    async def generate_batch(
        self,
        context: GrammarBatchJobContext,
    ) -> GrammarBatchExecutionResult: ...


class PydanticAIGrammarBatchExecutor:
    """T4.1c: one LLM call covering all units in a compact grammar batch.

    Builds a prompt that lists every unit's source text + anchor segments,
    asks the model to generate candidates across all units, then splits
    the output by ``unit_id`` and enforces per-unit budget.
    """

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _build_agent(self, *, model: Any) -> Agent:
        return Agent(
            model=model,
            output_type=GrammarBatchCandidateOutput,
            instructions=load_agent_instructions(GRAMMAR_PROMPT_AGENT_NAME),
            name="reader_layer_grammar_batch_agent",
            retries={"tools": 1, "output": 2},
        )

    async def _run_agent(self, agent: Agent, prompt: str) -> Any:
        return await agent.run(prompt)

    async def generate_batch(
        self,
        context: GrammarBatchJobContext,
    ) -> GrammarBatchExecutionResult:
        settings = self._settings or get_settings()
        prompt_version = get_prompt_version()
        if not str(settings.reader_grammar_bundle_model_profile or "").strip():
            raise GrammarExecutionError(
                (
                    "grammar batch executor is not configured; set "
                    "reader_grammar_bundle_model_profile or inject an explicit fake "
                    "executor for tests"
                ),
                retryable=False,
                failure_class="configuration",
                failure_code="grammar_batch_executor_unconfigured",
                prompt_version=prompt_version,
            )
        model, model_config = build_model_for_route(
            settings,
            MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE,
        )
        if model is None:
            raise GrammarExecutionError(
                "reader_layer_grammar_bundle model route is not configured",
                retryable=False,
                failure_class="configuration",
                failure_code="model_route_unavailable",
                prompt_version=prompt_version,
            )

        assert_real_llm_allowed(
            (
                "app.services.reader_orchestration.grammar_worker."
                "PydanticAIGrammarBatchExecutor"
            ),
            model_config=model_config,
        )

        agent = self._build_agent(model=model)
        try:
            result = await self._run_agent(
                agent, _build_grammar_batch_prompt(context)
            )
        except GrammarExecutionError:
            raise
        except Exception as exc:
            raise GrammarExecutionError(
                f"reader_layer_grammar_batch agent execution failed: {exc}",
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
            candidate_output = GrammarBatchCandidateOutput.model_validate(
                result.output
            )
        except ValidationError as exc:
            raise GrammarExecutionError(
                f"reader_layer_grammar_batch produced invalid structured output: {exc}",
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

        outputs, diagnostics = _split_batch_candidates_by_unit(
            context, candidate_output
        )
        usage_data = extract_run_usage(result)
        return GrammarBatchExecutionResult(
            outputs=outputs,
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


class FakeGrammarBatchExecutor:
    """No-op batch executor for tests that don't need real LLM calls."""

    async def generate_batch(
        self,
        context: GrammarBatchJobContext,
    ) -> GrammarBatchExecutionResult:
        outputs = [
            (unit.unit_id, GrammarBundleOutput()) for unit in context.units
        ]
        return GrammarBatchExecutionResult(outputs=outputs)


class GrammarBundleWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        layer_publisher: GrammarBundleLayerPublisher | None = None,
        executor: GrammarBundleExecutor | None = None,
        batch_executor: GrammarBatchExecutor | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._layer_publisher = layer_publisher or GrammarBundleLayerPublisher(pool=pool)
        self._executor = executor or PydanticAIGrammarBundleExecutor()
        # T4.1c: compact grammar batch executor for SHORT_BATCH / STRUCTURED_BATCH.
        # Defaults to PydanticAIGrammarBatchExecutor (real LLM); tests inject
        # FakeGrammarBatchExecutor to avoid real LLM calls.
        self._batch_executor = batch_executor or PydanticAIGrammarBatchExecutor()

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def claim_grammar_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> ClaimResult | None:
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=GRAMMAR_JOB_TYPE,
            target_type=GRAMMAR_TARGET_SCOPE,
            operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
        )
        if claim is None:
            return None
        if (
            claim.job_type != GRAMMAR_JOB_TYPE
            or claim.target_type != GRAMMAR_TARGET_SCOPE
            or not _fingerprint_matches_base(
                claim.operation_fingerprint, GRAMMAR_OPERATION_FINGERPRINT
            )
        ):
            raise RuntimeError(
                "grammar worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}/{claim.operation_fingerprint}"
            )
        await self._mark_run_running(claim.run_id)
        return claim

    async def claim_grammar_job_for_record(
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
            job_type=GRAMMAR_JOB_TYPE,
            target_type=GRAMMAR_TARGET_SCOPE,
            operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
            reading_record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if claim is None:
            return None
        if (
            claim.job_type != GRAMMAR_JOB_TYPE
            or claim.target_type != GRAMMAR_TARGET_SCOPE
            or not _fingerprint_matches_base(
                claim.operation_fingerprint, GRAMMAR_OPERATION_FINGERPRINT
            )
        ):
            raise RuntimeError(
                "grammar worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}/{claim.operation_fingerprint}"
            )
        await self._mark_run_running(claim.run_id)
        return claim

    async def heartbeat_grammar_job(
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

    async def process_next_grammar_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_GRAMMAR_RETRY_DELAY,
    ) -> GrammarJobProcessResult | None:
        claim = await self.claim_grammar_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_grammar_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_next_grammar_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_GRAMMAR_RETRY_DELAY,
    ) -> GrammarJobProcessResult | None:
        claim = await self.claim_grammar_job_for_record(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_grammar_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_claimed_grammar_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_GRAMMAR_RETRY_DELAY,
    ) -> GrammarJobProcessResult:
        context: GrammarJobContext | None = None

        try:
            context = await self._load_job_context(claim.job_id)
            execution = await self._executor.generate(context)
            try:
                bundle_output = GrammarBundleOutput.model_validate(execution.output)
            except ValidationError as exc:
                raise GrammarExecutionError(
                    f"grammar bundle produced invalid structured output: {exc}",
                    retryable=False,
                    failure_class="validation",
                    failure_code="grammar_bundle_output_invalid",
                    prompt_version=execution.prompt_version,
                    model_route=execution.model_route,
                    model_profile=execution.model_profile,
                    model_provider=execution.model_provider,
                    model_name=execution.model_name,
                ) from exc

            sanitized_output, diagnostics = _sanitize_grammar_bundle_output(
                context,
                bundle_output,
            )
            quality_json = _build_quality_json(
                sanitized_output,
                execution,
                diagnostics,
            )
            published_bundle = await self._layer_publisher.publish_unit_grammar_bundle(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                grammar_note_output=(
                    GrammarNoteLayerOutput(items=sanitized_output.grammar_notes)
                    if sanitized_output.grammar_notes
                    else None
                ),
                sentence_analysis_output=(
                    SentenceAnalysisLayerOutput(
                        items=sanitized_output.sentence_analyses
                    )
                    if sanitized_output.sentence_analyses
                    else None
                ),
                quality_json=quality_json,
            )
            event_id = await self._record_usage_event(
                context=context,
                execution=execution,
                published_bundle=published_bundle,
                status=STATUS_SUCCEEDED,
            )
            await end_worker_span_success(
                ai_usage_event_id=event_id,
                usage_data=execution.usage_data,
                model_route=execution.model_route,
                model_name=execution.model_name,
                model_provider=execution.model_provider,
                capability_code=CAPABILITY_READER_GRAMMAR_BUNDLE,
            )
            return GrammarJobProcessResult(
                claim=claim,
                context=context,
                status="succeeded",
                output=sanitized_output,
                published_bundle=published_bundle,
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
        except GrammarExecutionError as exc:
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
                return GrammarJobProcessResult(
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
            return GrammarJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )
        except Exception as exc:
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="failed_terminal",
                lease_token=claim.lease_token,
                failure_class="grammar_bundle_execution",
                failure_code=type(exc).__name__,
                failure_message=str(exc),
                rationale_code="grammar_bundle_execution_failed",
            )
            await self._mark_run_status(
                claim.run_id,
                status="failed_terminal",
                failure_class="grammar_bundle_execution",
                failure_code=type(exc).__name__,
                finished_at=datetime.now(UTC),
            )
            await self._record_failed_usage_event(
                context=context,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            await end_worker_span_generic_exception(layer="grammar_bundle", exc=exc)
            return GrammarJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )

    async def _load_job_context(self, job_id: UUID) -> GrammarJobContext:
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
                raise GrammarExecutionError(
                    f"grammar unit {row['target_key']} could not be sliced from base text",
                    retryable=False,
                    failure_class="validation",
                    failure_code="unit_slice_failed",
                )
            expected_hash = str(row["text_hash"])
            actual_hash = compute_text_range_hash(source_text)
            if actual_hash != expected_hash:
                raise GrammarExecutionError(
                    (
                        f"grammar unit {row['target_key']} hash mismatch: "
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

        anchor_segments: list[GrammarAnchorSegmentContext] = []
        for segment_row in segment_rows:
            segment_text = slice_by_utf16_offsets(
                source_text,
                int(segment_row["unit_start_utf16"]),
                int(segment_row["unit_end_utf16"]),
            )
            if segment_text is None or not segment_text:
                raise GrammarExecutionError(
                    (
                        f"grammar anchor segment {segment_row['anchor_segment_id']} "
                        "could not be sliced from unit text"
                    ),
                    retryable=False,
                    failure_class="validation",
                    failure_code="anchor_segment_slice_failed",
                )
            segment_hash = str(segment_row["text_hash"])
            if compute_text_range_hash(segment_text) != segment_hash:
                raise GrammarExecutionError(
                    (
                        f"grammar anchor segment {segment_row['anchor_segment_id']} "
                        "hash mismatch"
                    ),
                    retryable=False,
                    failure_class="validation",
                    failure_code="anchor_segment_hash_mismatch",
                )
            anchor_segments.append(
                GrammarAnchorSegmentContext(
                    anchor_segment_id=str(segment_row["anchor_segment_id"]),
                    sentence_id=str(
                        segment_row["sentence_id"] or segment_row["anchor_segment_id"]
                    ),
                    segment_type=str(segment_row["segment_type"]),
                    unit_start_utf16=int(segment_row["unit_start_utf16"]),
                    unit_end_utf16=int(segment_row["unit_end_utf16"]),
                    text_hash=segment_hash,
                    text=segment_text,
                )
            )

        if not anchor_segments:
            raise GrammarExecutionError(
                f"grammar unit {row['target_key']} has no anchor segments",
                retryable=False,
                failure_class="validation",
                failure_code="missing_anchor_segments",
            )

        input_json = row["input_json"]
        strategy_metadata = _validate_grammar_strategy_metadata(input_json)

        return GrammarJobContext(
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
            grammar_prompt_lines=strategy_metadata.grammar_prompt_lines,
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
        context: GrammarJobContext,
        execution: GrammarExecutionResult,
        published_bundle: PublishedGrammarBundle,
        status: str,
    ) -> UUID | None:
        layer_ids = [
            str(layer.layer_id)
            for layer in (
                published_bundle.grammar_note_layer,
                published_bundle.sentence_analysis_layer,
            )
            if layer is not None
        ]
        layer_types = [
            layer.layer_type
            for layer in (
                published_bundle.grammar_note_layer,
                published_bundle.sentence_analysis_layer,
            )
            if layer is not None
        ]
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_GRAMMAR_BUNDLE,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=status,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version=GRAMMAR_WORKFLOW_VERSION,
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
                    "published_layer_ids": layer_ids,
                    "published_layer_types": layer_types,
                    "no_op": published_bundle.no_op,
                },
            )
        )

    async def _record_failed_usage_event(
        self,
        *,
        context: GrammarJobContext | None,
        error_code: str,
        error_message: str,
        prompt_version: str | None = None,
        model_route: str = GRAMMAR_MODEL_ROUTE,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> UUID | None:
        if context is None:
            return None
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_GRAMMAR_BUNDLE,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_FAILED,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version=GRAMMAR_WORKFLOW_VERSION,
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
                    "published_layer_ids": [],
                    "published_layer_types": [],
                    "no_op": False,
                },
            )
        )

    # ------------------------------------------------------------------#
    # T4.1c: compact grammar batch path (SHORT_BATCH / STRUCTURED_BATCH)
    # ------------------------------------------------------------------#

    async def claim_grammar_batch_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> ClaimResult | None:
        """Claim a ``build_grammar_bundle`` / ``unit_range`` batch job for the record."""
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=GRAMMAR_BATCH_JOB_TYPE,
            target_type=GRAMMAR_BATCH_TARGET_SCOPE,
            operation_fingerprint=None,
            reading_record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if claim is None:
            return None
        if (
            claim.job_type != GRAMMAR_BATCH_JOB_TYPE
            or claim.target_type != GRAMMAR_BATCH_TARGET_SCOPE
        ):
            raise RuntimeError(
                "grammar batch worker claimed unsupported job "
                f"{claim.job_type}/{claim.target_type}/{claim.operation_fingerprint}"
            )
        await self._mark_run_running(claim.run_id)
        return claim

    async def process_next_grammar_batch_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_GRAMMAR_RETRY_DELAY,
    ) -> GrammarBatchJobProcessResult | None:
        """Claim and process the next grammar batch job for the record."""
        claim = await self.claim_grammar_batch_job_for_record(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_grammar_batch_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_claimed_grammar_batch_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_GRAMMAR_RETRY_DELAY,
    ) -> GrammarBatchJobProcessResult:
        """Run the batch LLM call and publish N per-unit grammar layers.

        Exception handling mirrors ``process_claimed_grammar_job``:
        ``FenceViolationError`` → ``superseded``;
        ``GrammarExecutionError`` (retryable) → ``retry_later``;
        ``GrammarExecutionError`` (non-retryable) → ``failed_terminal``;
        any other ``Exception`` → ``failed_terminal``.
        """
        context: GrammarBatchJobContext | None = None

        try:
            context = await self._load_batch_job_context(claim.job_id)
            execution = await self._batch_executor.generate_batch(context)
            published_batch = (
                await self._layer_publisher.publish_article_grammar_batch(
                    job_id=claim.job_id,
                    lease_token=claim.lease_token,
                    outputs=execution.outputs,
                    quality_json=_build_batch_quality_json(
                        execution,
                        unit_count=len(context.units),
                    ),
                )
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
                capability_code=CAPABILITY_READER_GRAMMAR_BUNDLE,
            )
            return GrammarBatchJobProcessResult(
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
        except GrammarExecutionError as exc:
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
                return GrammarBatchJobProcessResult(
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
            return GrammarBatchJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )
        except Exception as exc:
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status="failed_terminal",
                lease_token=claim.lease_token,
                failure_class="grammar_batch_worker",
                failure_code=type(exc).__name__,
                failure_message=str(exc),
                rationale_code="grammar_batch_worker_unexpected_error",
            )
            await self._mark_run_status(
                claim.run_id,
                status="failed_terminal",
                failure_class="grammar_batch_worker",
                failure_code=type(exc).__name__,
                finished_at=datetime.now(UTC),
            )
            await end_worker_span_generic_exception(
                layer="grammar_bundle", exc=exc
            )
            return GrammarBatchJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
            )

    async def _load_batch_job_context(
        self,
        job_id: UUID,
    ) -> GrammarBatchJobContext:
        """Load all target units' text + anchor segments for a batch job."""
        async with self.get_pool().acquire() as conn:
            job_row = await conn.fetchrow(
                """
                SELECT job.id,
                       job.run_id,
                       job.reading_record_id,
                       job.user_id,
                       job.base_id,
                       job.expected_generation,
                       job.operation_fingerprint,
                       job.input_json,
                       run.envelope_json,
                       base.language AS source_language,
                       base.text AS base_text
                FROM reader_jobs job
                JOIN reader_runs run
                  ON run.id = job.run_id
                JOIN reading_bases base
                  ON base.id = job.base_id
                 AND base.reading_record_id = job.reading_record_id
                WHERE job.id = $1
                """,
                job_id,
            )
            if job_row is None:
                raise LookupError(f"reader job {job_id} not found")

            input_json = job_row["input_json"]
            target_unit_ids: list[str] = list(
                input_json.get("target_unit_ids") or []
            )
            if not target_unit_ids:
                raise GrammarExecutionError(
                    f"grammar batch job {job_id} has no target_unit_ids",
                    retryable=False,
                    failure_class="validation",
                    failure_code="batch_missing_target_unit_ids",
                )

            base_text = str(job_row["base_text"])

            units: list[GrammarBatchUnitContext] = []
            for unit_id in target_unit_ids:
                unit_row = await conn.fetchrow(
                    """
                    SELECT unit_id, order_index,
                           base_start_utf16, base_end_utf16, text_hash
                    FROM reading_units
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND unit_id = $3
                    """,
                    job_row["reading_record_id"],
                    job_row["base_id"],
                    unit_id,
                )
                if unit_row is None:
                    raise GrammarExecutionError(
                        f"grammar batch target unit {unit_id} not found",
                        retryable=False,
                        failure_class="validation",
                        failure_code="batch_unit_not_found",
                    )
                source_text = slice_by_utf16_offsets(
                    base_text,
                    int(unit_row["base_start_utf16"]),
                    int(unit_row["base_end_utf16"]),
                )
                if source_text is None or not source_text:
                    raise GrammarExecutionError(
                        f"grammar batch unit {unit_id} could not be sliced",
                        retryable=False,
                        failure_class="validation",
                        failure_code="batch_unit_slice_failed",
                    )
                if compute_text_range_hash(source_text) != str(
                    unit_row["text_hash"]
                ):
                    raise GrammarExecutionError(
                        f"grammar batch unit {unit_id} hash mismatch",
                        retryable=False,
                        failure_class="validation",
                        failure_code="batch_unit_hash_mismatch",
                    )

                segment_rows = await conn.fetch(
                    """
                    SELECT anchor_segment_id,
                           sentence_id,
                           segment_type,
                           unit_start_utf16,
                           unit_end_utf16,
                           text_hash
                    FROM anchor_segments
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND unit_id = $3
                    ORDER BY order_index ASC
                    """,
                    job_row["reading_record_id"],
                    job_row["base_id"],
                    unit_id,
                )
                anchor_segments: list[GrammarAnchorSegmentContext] = []
                for seg_row in segment_rows:
                    seg_text = slice_by_utf16_offsets(
                        source_text,
                        int(seg_row["unit_start_utf16"]),
                        int(seg_row["unit_end_utf16"]),
                    )
                    if seg_text is None or not seg_text:
                        raise GrammarExecutionError(
                            f"grammar batch anchor {seg_row['anchor_segment_id']} slice failed",
                            retryable=False,
                            failure_class="validation",
                            failure_code="batch_anchor_slice_failed",
                        )
                    anchor_segments.append(
                        GrammarAnchorSegmentContext(
                            anchor_segment_id=str(seg_row["anchor_segment_id"]),
                            sentence_id=str(
                                seg_row["sentence_id"]
                                or seg_row["anchor_segment_id"]
                            ),
                            segment_type=str(seg_row["segment_type"]),
                            unit_start_utf16=int(seg_row["unit_start_utf16"]),
                            unit_end_utf16=int(seg_row["unit_end_utf16"]),
                            text_hash=str(seg_row["text_hash"]),
                            text=seg_text,
                        )
                    )
                if not anchor_segments:
                    raise GrammarExecutionError(
                        f"grammar batch unit {unit_id} has no anchor segments",
                        retryable=False,
                        failure_class="validation",
                        failure_code="batch_missing_anchor_segments",
                    )
                units.append(
                    GrammarBatchUnitContext(
                        unit_id=unit_id,
                        order_index=int(unit_row["order_index"]),
                        source_text=source_text,
                        text_hash=str(unit_row["text_hash"]),
                        anchor_segments=tuple(anchor_segments),
                    )
                )

        strategy_metadata = _validate_grammar_strategy_metadata(input_json)
        envelope_json = job_row["envelope_json"] or {}
        article_route = str(
            input_json.get("article_route")
            or envelope_json.get("article_route")
            or ""
        )
        document_features_raw = envelope_json.get("document_features")
        document_features: dict[str, Any] | None = (
            dict(document_features_raw)
            if isinstance(document_features_raw, Mapping)
            else None
        )
        return GrammarBatchJobContext(
            job_id=job_row["id"],
            run_id=job_row["run_id"],
            reading_record_id=job_row["reading_record_id"],
            user_id=job_row["user_id"],
            base_id=job_row["base_id"],
            expected_generation=int(job_row["expected_generation"]),
            operation_fingerprint=str(job_row["operation_fingerprint"]),
            source_language=str(job_row["source_language"] or "en"),
            units=tuple(units),
            reading_goal=strategy_metadata.reading_goal,
            reading_variant=strategy_metadata.reading_variant,
            strategy_version=strategy_metadata.strategy_version,
            strategy_hash=strategy_metadata.strategy_hash,
            layer_policy_hash=strategy_metadata.layer_policy_hash,
            grammar_prompt_lines=strategy_metadata.grammar_prompt_lines,
            article_route=article_route,
            document_features=document_features,
        )

    async def _record_batch_usage_event(
        self,
        *,
        context: GrammarBatchJobContext,
        execution: GrammarBatchExecutionResult,
        published_batch: PublishedGrammarBatch,
        status: str,
    ) -> UUID | None:
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_GRAMMAR_BUNDLE,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=status,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version=GRAMMAR_WORKFLOW_VERSION,
                prompt_version=execution.prompt_version,
                model_route=execution.model_route,
                model_profile_id=execution.model_profile,
                model_profile=execution.model_profile,
                model_provider=execution.model_provider,
                model_name=execution.model_name,
                planner_kind="llm_worker",
                operation_fingerprint=context.operation_fingerprint,
                metadata_json={
                    "base_id": str(context.base_id),
                    "unit_count": len(context.units),
                    "source_language": context.source_language,
                    "published_layer_ids": published_batch.layer_ids,
                    "published_layer_types": published_batch.layer_types,
                    "no_op": published_batch.no_op,
                },
            )
        )

    async def _record_batch_failed_usage_event(
        self,
        *,
        context: GrammarBatchJobContext | None,
        error_code: str,
        error_message: str,
        prompt_version: str | None = None,
        model_route: str = GRAMMAR_MODEL_ROUTE,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> UUID | None:
        if context is None:
            return None
        return await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_GRAMMAR_BUNDLE,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_FAILED,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version=GRAMMAR_WORKFLOW_VERSION,
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
                    "unit_count": len(context.units),
                    "source_language": context.source_language,
                    "published_layer_ids": [],
                    "published_layer_types": [],
                    "no_op": False,
                },
            )
        )


def _build_grammar_prompt(context: GrammarJobContext) -> str:
    strategy_section = _format_grammar_strategy_section(context)
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
        "Generate high-value grammar bundle annotations for a single reading unit.\n"
        f"source_language: {context.source_language}\n"
        f"unit_id: {context.unit_id}\n"
        f"max_grammar_notes: {MAX_GRAMMAR_NOTE_ITEMS}\n"
        f"max_sentence_analyses: {MAX_SENTENCE_ANALYSIS_ITEMS}\n"
        f"{strategy_section}"
        "Return only the structured candidate output.\n"
        "<source_text>\n"
        f"{context.source_text}\n"
        "</source_text>\n"
        "<anchor_segments_json>\n"
        f"{json.dumps(anchor_segments, ensure_ascii=False)}\n"
        "</anchor_segments_json>"
    )


def _format_grammar_strategy_section(context: GrammarJobContext) -> str:
    """Format the concrete grammar_bundle policy lines as a prompt section.

    The strategy section carries the resolved variant-first policy lines
    (from ``reader_variants.yaml`` via ``resolve_reader_variant_strategy``)
    so the grammar bundle agent can vary its grammar_note / sentence_analysis
    output by ``reading_goal`` / ``reading_variant``. The accompanying hashes
    are included for traceability and so that prompt-level evals can group
    by strategy.
    """
    lines_bullet = "\n".join(
        f"- {line}" for line in context.grammar_prompt_lines
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
class _GrammarStrategyMetadata:
    """Validated strategy metadata extracted from a grammar job's
    input_json and cross-checked against the live resolver."""

    reading_goal: str
    reading_variant: str
    strategy_version: str
    strategy_hash: str
    layer_policy_hash: str
    grammar_prompt_lines: tuple[str, ...]


def _validate_grammar_strategy_metadata(
    input_json: Any,
) -> _GrammarStrategyMetadata:
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
          :class:`GrammarExecutionError` with failure_class
          ``strategy_resolution``.
        - ``strategy_version``, ``strategy_hash`` and
          ``layer_policy_hash`` from input_json must match the resolver
          output exactly. Any mismatch fails closed with a dedicated
          failure_code.
    """
    if not isinstance(input_json, Mapping):
        raise GrammarExecutionError(
            "grammar job input_json is not a mapping; "
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
        raise GrammarExecutionError(
            "grammar job input_json is missing strategy metadata: "
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
        raise GrammarExecutionError(
            f"grammar strategy resolver rejected pair "
            f"({reading_goal!r}, {reading_variant!r}): {exc}",
            retryable=False,
            failure_class="strategy_resolution",
            failure_code="strategy_resolver_error",
        ) from exc

    if strategy.strategy_version != expected_strategy_version:
        raise GrammarExecutionError(
            f"grammar strategy_version mismatch: input_json has "
            f"{expected_strategy_version!r} but resolver produced "
            f"{strategy.strategy_version!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_STRATEGY_VERSION_MISMATCH_CODE,
        )

    if strategy.strategy_hash != expected_strategy_hash:
        raise GrammarExecutionError(
            f"grammar strategy_hash mismatch: input_json has "
            f"{expected_strategy_hash!r} but resolver produced "
            f"{strategy.strategy_hash!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_STRATEGY_HASH_MISMATCH_CODE,
        )

    layer = strategy.layers.get(_GRAMMAR_LAYER_NAME)
    if layer is None:
        # Defensive: the resolver guarantees all REQUIRED_LAYERS are
        # present. Fail closed if a future code path violates that.
        raise GrammarExecutionError(
            f"resolved strategy has no layer {_GRAMMAR_LAYER_NAME!r}",
            retryable=False,
            failure_class="strategy_resolution",
            failure_code="strategy_resolver_error",
        )

    if layer.policy_hash != expected_layer_policy_hash:
        raise GrammarExecutionError(
            f"grammar layer_policy_hash mismatch: input_json has "
            f"{expected_layer_policy_hash!r} but resolver produced "
            f"{layer.policy_hash!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_LAYER_POLICY_HASH_MISMATCH_CODE,
        )

    return _GrammarStrategyMetadata(
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        grammar_prompt_lines=layer.prompt_lines,
    )


def _build_grammar_output_from_candidates(
    context: GrammarJobContext,
    candidate_output: GrammarBundleCandidateOutput,
) -> tuple[GrammarBundleOutput, dict[str, Any]]:
    segments_by_id = {
        segment.anchor_segment_id: segment
        for segment in context.anchor_segments
    }
    grammar_notes: list[GrammarNoteItem] = []
    sentence_analyses: list[SentenceAnalysisItem] = []
    skipped_items: list[dict[str, Any]] = []

    for item_index, item in enumerate(candidate_output.grammar_notes):
        resolved_spans: list[ReaderTextRangeAnchor] = []
        note_rejected = False
        for span_index, span in enumerate(item.spans):
            segment = segments_by_id.get(span.anchor_segment_id)
            if segment is not None and segment.segment_type == "fallback_window":
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=item_index,
                        item_type=item.item_type,
                        anchor_segment_id=span.anchor_segment_id,
                        selected_text=span.selected_text,
                        reason_code="boundary_low_fallback_window",
                        span_index=span_index,
                    )
                )
                note_rejected = True
                break
            resolved_anchor, reason_code = _resolve_candidate_anchor(
                context=context,
                segments_by_id=segments_by_id,
                anchor_segment_id=span.anchor_segment_id,
                selected_text=span.selected_text,
            )
            if resolved_anchor is None:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=item_index,
                        item_type=item.item_type,
                        anchor_segment_id=span.anchor_segment_id,
                        selected_text=span.selected_text,
                        reason_code=reason_code,
                        span_index=span_index,
                    )
                )
                note_rejected = True
                break
            resolved_spans.append(resolved_anchor)

        if note_rejected:
            continue

        try:
            grammar_notes.append(
                GrammarNoteItem(
                    spans=resolved_spans,
                    grammar_point=item.grammar_point,
                    pattern=item.pattern,
                    note=item.note,
                )
            )
        except ValidationError:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.spans[0].anchor_segment_id,
                    selected_text=item.spans[0].selected_text,
                    reason_code="resolved_item_invalid",
                )
            )

    for item_index, item in enumerate(candidate_output.sentence_analyses):
        segment = segments_by_id.get(item.anchor_segment_id)
        if segment is not None and segment.segment_type == "fallback_window":
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
        resolved_anchor, reason_code = _resolve_candidate_anchor(
            context=context,
            segments_by_id=segments_by_id,
            anchor_segment_id=item.anchor_segment_id,
            selected_text=item.selected_text,
        )
        if resolved_anchor is None:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code=reason_code,
                )
            )
            continue

        try:
            sentence_analyses.append(
                SentenceAnalysisItem(
                    anchor=resolved_anchor,
                    label=item.label,
                    analysis=item.analysis,
                    chunks=[
                        SentenceAnalysisChunk(
                            order=chunk_index + 1,
                            label=chunk.label,
                            text=chunk.text,
                        )
                        for chunk_index, chunk in enumerate(item.chunks)
                    ],
                )
            )
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

    trimmed_skipped_items = _trim_skipped_diagnostics(skipped_items)
    return GrammarBundleOutput(
        grammar_notes=grammar_notes,
        sentence_analyses=sentence_analyses,
    ), {
        "candidate_grammar_note_count": len(candidate_output.grammar_notes),
        "candidate_sentence_analysis_count": len(candidate_output.sentence_analyses),
        "resolved_grammar_note_count": len(grammar_notes),
        "resolved_sentence_analysis_count": len(sentence_analyses),
        "skipped_item_count": len(skipped_items),
        "skipped_items": trimmed_skipped_items,
        "skipped_items_truncated_count": max(
            0,
            len(skipped_items) - len(trimmed_skipped_items),
        ),
    }


def _resolve_candidate_anchor(
    *,
    context: GrammarJobContext,
    segments_by_id: dict[str, GrammarAnchorSegmentContext],
    anchor_segment_id: str,
    selected_text: str,
) -> tuple[ReaderTextRangeAnchor | None, str]:
    segment = segments_by_id.get(anchor_segment_id)
    if segment is None:
        return None, "anchor_segment_unknown"

    occurrences = _find_unique_segment_occurrences(segment.text, selected_text)
    if not occurrences:
        return None, "selected_text_not_found"
    if len(occurrences) > 1:
        return None, "selected_text_ambiguous"

    segment_start, segment_end = occurrences[0]
    start_offset = segment.unit_start_utf16 + segment_start
    end_offset = segment.unit_start_utf16 + segment_end
    if start_offset < segment.unit_start_utf16 or end_offset > segment.unit_end_utf16:
        return None, "selected_text_outside_segment"

    resolved_text = slice_by_utf16_offsets(
        context.source_text,
        start_offset,
        end_offset,
    )
    if resolved_text is None or resolved_text != selected_text:
        return None, "selected_text_slice_mismatch"

    return (
        ReaderTextRangeAnchor(
            base_id=str(context.base_id),
            unit_id=context.unit_id,
            anchor_segment_id=segment.anchor_segment_id,
            sentence_id=segment.sentence_id,
            segment_type=segment.segment_type,  # type: ignore[arg-type]
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=resolved_text,
            text_hash=compute_text_range_hash(resolved_text),
        ),
        "",
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


def _build_skip_diagnostic(
    *,
    item_index: int,
    item_type: str,
    anchor_segment_id: str,
    selected_text: str,
    reason_code: str,
    span_index: int | None = None,
) -> dict[str, Any]:
    diagnostic = {
        "item_index": item_index,
        "item_type": item_type,
        "anchor_segment_id": anchor_segment_id,
        "selected_text": _truncate_diagnostic_text(selected_text),
        "reason_code": reason_code,
    }
    if span_index is not None:
        diagnostic["span_index"] = span_index
    return diagnostic


def _trim_skipped_diagnostics(skipped_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return skipped_items[:MAX_GRAMMAR_DIAGNOSTIC_ITEMS]


def _truncate_diagnostic_text(text: str) -> str:
    if len(text) <= MAX_GRAMMAR_DIAGNOSTIC_TEXT_LENGTH:
        return text
    return text[: MAX_GRAMMAR_DIAGNOSTIC_TEXT_LENGTH - 3] + "..."


def _sanitize_grammar_bundle_output(
    context: GrammarJobContext,
    output: GrammarBundleOutput,
) -> tuple[GrammarBundleOutput, dict[str, Any]]:
    segments_by_id = {
        segment.anchor_segment_id: segment
        for segment in context.anchor_segments
    }
    grammar_notes: list[GrammarNoteItem] = []
    sentence_analyses: list[SentenceAnalysisItem] = []
    skipped_items: list[dict[str, Any]] = []

    for item_index, item in enumerate(output.grammar_notes):
        fallback_span_ids: list[str] = []
        for span in item.spans:
            segment = segments_by_id.get(span.anchor_segment_id)
            if segment is not None and segment.segment_type == "fallback_window":
                fallback_span_ids.append(span.anchor_segment_id)
        if fallback_span_ids:
            skipped_items.append(
                {
                    "item_index": item_index,
                    "item_type": item.item_type,
                    "anchor_segment_ids": fallback_span_ids,
                    "reason_code": "boundary_low_fallback_window",
                }
            )
            continue
        grammar_notes.append(item)

    for item_index, item in enumerate(output.sentence_analyses):
        segment = segments_by_id.get(item.anchor.anchor_segment_id)
        if segment is not None and segment.segment_type == "fallback_window":
            skipped_items.append(
                {
                    "item_index": item_index,
                    "item_type": item.item_type,
                    "anchor_segment_id": item.anchor.anchor_segment_id,
                    "reason_code": "boundary_low_fallback_window",
                }
            )
            continue
        sentence_analyses.append(item)

    trimmed_skipped_items = skipped_items[:MAX_GRAMMAR_DIAGNOSTIC_ITEMS]
    diagnostics = {
        "candidate_grammar_note_count": len(output.grammar_notes),
        "candidate_sentence_analysis_count": len(output.sentence_analyses),
        "grammar_note_count": len(grammar_notes),
        "sentence_analysis_count": len(sentence_analyses),
        "skipped_item_count": len(skipped_items),
        "skipped_items": trimmed_skipped_items,
        "skipped_items_truncated_count": max(
            0,
            len(skipped_items) - len(trimmed_skipped_items),
        ),
    }
    return GrammarBundleOutput(
        grammar_notes=grammar_notes,
        sentence_analyses=sentence_analyses,
    ), diagnostics


def _build_quality_json(
    output: GrammarBundleOutput,
    execution: GrammarExecutionResult,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    quality_json: dict[str, Any] = {
        "grammar_note_count": len(output.grammar_notes),
        "sentence_analysis_count": len(output.sentence_analyses),
        "diagnostics": diagnostics,
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
        quality_json["executor_diagnostics"] = execution.diagnostics
    return quality_json


# ---------------------------------------------------------------------------#
# T4.1c: compact grammar batch helpers
# ---------------------------------------------------------------------------#


def _format_grammar_batch_strategy_section(
    context: GrammarBatchJobContext,
) -> str:
    """Format the grammar_bundle policy lines for the batch prompt.

    Mirrors :func:`_format_grammar_strategy_section` but reads from the
    batch context. The strategy metadata is shared across all units in
    the batch (same ``reading_goal`` / ``reading_variant`` / policy hash).
    """
    lines_bullet = "\n".join(
        f"- {line}" for line in context.grammar_prompt_lines
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


def _build_grammar_batch_prompt(context: GrammarBatchJobContext) -> str:
    """Build a single prompt covering all units in a compact grammar batch.

    Lists every unit's ``source_text`` + ``anchor_segments`` so the model
    can generate ``grammar_note`` / ``sentence_analysis`` candidates across
    all units in one LLM call. Each candidate must reference an
    ``anchor_segment_id`` from one of the listed units; the splitter
    (:func:`_split_batch_candidates_by_unit`) uses that mapping to route
    each candidate back to its owning unit.

    T4.1b route identity (``article_route``) and a compact
    ``document_features`` summary are included so the model can adapt
    candidate density / focus to the article tier (short vs structured).
    """
    strategy_section = _format_grammar_batch_strategy_section(context)
    units_payload = []
    for unit in context.units:
        anchor_segments = [
            {
                "anchor_segment_id": segment.anchor_segment_id,
                "sentence_id": segment.sentence_id,
                "segment_type": segment.segment_type,
                "unit_start_utf16": segment.unit_start_utf16,
                "unit_end_utf16": segment.unit_end_utf16,
                "text": segment.text,
            }
            for segment in unit.anchor_segments
        ]
        units_payload.append(
            {
                "unit_id": unit.unit_id,
                "order_index": unit.order_index,
                "source_text": unit.source_text,
                "anchor_segments": anchor_segments,
            }
        )
    # Compact document_features summary for the prompt — only the signals
    # the model can usefully adapt to. Full profile stays in envelope_json
    # for auditability.
    doc_features_lines: list[str] = []
    if context.document_features:
        for key in (
            "estimated_word_count",
            "estimated_token_count",
            "unit_count",
            "paragraph_count",
            "heading_count",
            "structural_noise_ratio",
        ):
            value = context.document_features.get(key)
            if value is not None:
                doc_features_lines.append(f"{key}: {value}")
    doc_features_section = ""
    if doc_features_lines:
        doc_features_section = (
            "<document_features>\n"
            + "\n".join(doc_features_lines)
            + "\n</document_features>\n"
        )
    return (
        "Generate high-value grammar bundle annotations for multiple reading units "
        "in a single batch.\n"
        f"source_language: {context.source_language}\n"
        f"article_route: {context.article_route}\n"
        f"unit_count: {len(context.units)}\n"
        f"max_grammar_notes_per_unit: {MAX_GRAMMAR_NOTE_ITEMS}\n"
        f"max_sentence_analyses_per_unit: {MAX_SENTENCE_ANALYSIS_ITEMS}\n"
        f"{doc_features_section}"
        f"{strategy_section}"
        "Each grammar_note / sentence_analysis candidate must reference an "
        "anchor_segment_id from one of the units listed below. The "
        "anchor_segment_id determines which unit the candidate belongs to. "
        "Return only the structured candidate output.\n"
        "<units_json>\n"
        f"{json.dumps(units_payload, ensure_ascii=False)}\n"
        "</units_json>"
    )


def _resolve_batch_candidate_anchor(
    *,
    batch_context: GrammarBatchJobContext,
    unit_context: GrammarBatchUnitContext,
    segments_by_id: dict[str, GrammarAnchorSegmentContext],
    anchor_segment_id: str,
    selected_text: str,
) -> tuple[ReaderTextRangeAnchor | None, str]:
    """Resolve a candidate anchor within a batch unit's source text.

    Mirrors :func:`_resolve_candidate_anchor` but operates on the batch
    unit context (each unit has its own ``source_text`` / ``unit_id``;
    ``base_id`` comes from the batch context).
    """
    segment = segments_by_id.get(anchor_segment_id)
    if segment is None:
        return None, "anchor_segment_unknown"

    occurrences = _find_unique_segment_occurrences(segment.text, selected_text)
    if not occurrences:
        return None, "selected_text_not_found"
    if len(occurrences) > 1:
        return None, "selected_text_ambiguous"

    segment_start, segment_end = occurrences[0]
    start_offset = segment.unit_start_utf16 + segment_start
    end_offset = segment.unit_start_utf16 + segment_end
    if start_offset < segment.unit_start_utf16 or end_offset > segment.unit_end_utf16:
        return None, "selected_text_outside_segment"

    resolved_text = slice_by_utf16_offsets(
        unit_context.source_text,
        start_offset,
        end_offset,
    )
    if resolved_text is None or resolved_text != selected_text:
        return None, "selected_text_slice_mismatch"

    return (
        ReaderTextRangeAnchor(
            base_id=str(batch_context.base_id),
            unit_id=unit_context.unit_id,
            anchor_segment_id=segment.anchor_segment_id,
            sentence_id=segment.sentence_id,
            segment_type=segment.segment_type,  # type: ignore[arg-type]
            start_offset=start_offset,
            end_offset=end_offset,
            selected_text=resolved_text,
            text_hash=compute_text_range_hash(resolved_text),
        ),
        "",
    )


def _split_batch_candidates_by_unit(
    context: GrammarBatchJobContext,
    candidate_output: GrammarBatchCandidateOutput,
) -> tuple[list[tuple[str, GrammarBundleOutput]], dict[str, Any]]:
    """Split batch candidates by ``unit_id`` and enforce per-unit budget.

    Each candidate references an ``anchor_segment_id`` which maps to
    exactly one unit via ``context.units[].anchor_segments``. Candidates
    whose ``anchor_segment_id`` is unknown or whose owning unit has
    reached its per-unit budget are skipped and recorded in diagnostics.

    Returns per-unit outputs (in unit reading order) + a diagnostics dict
    for the publish quality_json.
    """
    # Build anchor_segment_id -> (unit_context, segment) map.
    units_by_segment: dict[
        str, tuple[GrammarBatchUnitContext, GrammarAnchorSegmentContext]
    ] = {}
    for unit in context.units:
        for segment in unit.anchor_segments:
            units_by_segment[segment.anchor_segment_id] = (unit, segment)

    unit_notes: dict[str, list[GrammarNoteItem]] = {
        u.unit_id: [] for u in context.units
    }
    unit_analyses: dict[str, list[SentenceAnalysisItem]] = {
        u.unit_id: [] for u in context.units
    }
    skipped_items: list[dict[str, Any]] = []

    for item_index, item in enumerate(candidate_output.grammar_notes):
        first_span = item.spans[0] if item.spans else None
        if first_span is None:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id="",
                    selected_text="",
                    reason_code="grammar_note_no_spans",
                )
            )
            continue
        mapping = units_by_segment.get(first_span.anchor_segment_id)
        if mapping is None:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=first_span.anchor_segment_id,
                    selected_text=first_span.selected_text,
                    reason_code="anchor_segment_unknown",
                )
            )
            continue
        unit_context, _ = mapping
        if len(unit_notes[unit_context.unit_id]) >= MAX_GRAMMAR_NOTE_ITEMS:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=first_span.anchor_segment_id,
                    selected_text=first_span.selected_text,
                    reason_code="per_unit_budget_exceeded",
                )
            )
            continue

        segments_by_id = {
            s.anchor_segment_id: s for s in unit_context.anchor_segments
        }
        resolved_spans: list[ReaderTextRangeAnchor] = []
        note_rejected = False
        for span_index, span in enumerate(item.spans):
            segment = segments_by_id.get(span.anchor_segment_id)
            if segment is not None and segment.segment_type == "fallback_window":
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=item_index,
                        item_type=item.item_type,
                        anchor_segment_id=span.anchor_segment_id,
                        selected_text=span.selected_text,
                        reason_code="boundary_low_fallback_window",
                        span_index=span_index,
                    )
                )
                note_rejected = True
                break
            resolved_anchor, reason_code = _resolve_batch_candidate_anchor(
                batch_context=context,
                unit_context=unit_context,
                segments_by_id=segments_by_id,
                anchor_segment_id=span.anchor_segment_id,
                selected_text=span.selected_text,
            )
            if resolved_anchor is None:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=item_index,
                        item_type=item.item_type,
                        anchor_segment_id=span.anchor_segment_id,
                        selected_text=span.selected_text,
                        reason_code=reason_code,
                        span_index=span_index,
                    )
                )
                note_rejected = True
                break
            resolved_spans.append(resolved_anchor)

        if note_rejected:
            continue

        try:
            unit_notes[unit_context.unit_id].append(
                GrammarNoteItem(
                    spans=resolved_spans,
                    grammar_point=item.grammar_point,
                    pattern=item.pattern,
                    note=item.note,
                )
            )
        except ValidationError:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=first_span.anchor_segment_id,
                    selected_text=first_span.selected_text,
                    reason_code="resolved_item_invalid",
                )
            )

    for item_index, item in enumerate(candidate_output.sentence_analyses):
        mapping = units_by_segment.get(item.anchor_segment_id)
        if mapping is None:
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
        unit_context, _ = mapping
        if len(unit_analyses[unit_context.unit_id]) >= MAX_SENTENCE_ANALYSIS_ITEMS:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code="per_unit_budget_exceeded",
                )
            )
            continue

        segments_by_id = {
            s.anchor_segment_id: s for s in unit_context.anchor_segments
        }
        segment = segments_by_id.get(item.anchor_segment_id)
        if segment is not None and segment.segment_type == "fallback_window":
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
        resolved_anchor, reason_code = _resolve_batch_candidate_anchor(
            batch_context=context,
            unit_context=unit_context,
            segments_by_id=segments_by_id,
            anchor_segment_id=item.anchor_segment_id,
            selected_text=item.selected_text,
        )
        if resolved_anchor is None:
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=item_index,
                    item_type=item.item_type,
                    anchor_segment_id=item.anchor_segment_id,
                    selected_text=item.selected_text,
                    reason_code=reason_code,
                )
            )
            continue

        try:
            unit_analyses[unit_context.unit_id].append(
                SentenceAnalysisItem(
                    anchor=resolved_anchor,
                    label=item.label,
                    analysis=item.analysis,
                    chunks=[
                        SentenceAnalysisChunk(
                            order=chunk_index + 1,
                            label=chunk.label,
                            text=chunk.text,
                        )
                        for chunk_index, chunk in enumerate(item.chunks)
                    ],
                )
            )
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

    outputs = [
        (
            unit.unit_id,
            GrammarBundleOutput(
                grammar_notes=unit_notes[unit.unit_id],
                sentence_analyses=unit_analyses[unit.unit_id],
            ),
        )
        for unit in context.units
    ]

    trimmed_skipped = _trim_skipped_diagnostics(skipped_items)
    diagnostics: dict[str, Any] = {
        "candidate_grammar_note_count": len(candidate_output.grammar_notes),
        "candidate_sentence_analysis_count": len(
            candidate_output.sentence_analyses
        ),
        "unit_count": len(context.units),
        "per_unit_grammar_note_counts": {
            u.unit_id: len(unit_notes[u.unit_id]) for u in context.units
        },
        "per_unit_sentence_analysis_counts": {
            u.unit_id: len(unit_analyses[u.unit_id]) for u in context.units
        },
        "skipped_item_count": len(skipped_items),
        "skipped_items": trimmed_skipped,
        "skipped_items_truncated_count": max(
            0,
            len(skipped_items) - len(trimmed_skipped),
        ),
    }
    return outputs, diagnostics


def _build_batch_quality_json(
    execution: GrammarBatchExecutionResult,
    unit_count: int,
) -> dict[str, Any]:
    """Build the quality_json for a compact grammar batch publish.

    Aggregates per-unit counts into batch-level totals and records
    model / prompt metadata for traceability.
    """
    total_grammar_notes = sum(
        len(output.grammar_notes) for _, output in execution.outputs
    )
    total_sentence_analyses = sum(
        len(output.sentence_analyses) for _, output in execution.outputs
    )
    quality_json: dict[str, Any] = {
        "batch": True,
        "unit_count": unit_count,
        "grammar_note_count": total_grammar_notes,
        "sentence_analysis_count": total_sentence_analyses,
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
