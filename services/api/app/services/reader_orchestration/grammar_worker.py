from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_ai import Agent

from app.config.settings import Settings, get_settings
from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.database import connection as db_connection
from app.llm.agent_runner import extract_run_usage, run_reader_scoped_agent
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
    update_ai_usage_event_outcome,
)
from app.services.ai_usage.execution_diagnostics import with_execution_correlation
from app.services.model_execution_journal import (
    CapturedReceipt,
    CaptureEnvelopeConflictError,
    ExecutionIdentity,
    PayloadContractError,
    decode_resume_payload,
    decode_usage_event_draft,
    prepare_capture_envelope,
)
from app.services.model_execution_journal.service import ModelExecutionJournalService
from app.services.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
)
from app.services.reader_orchestration.grammar_candidate_policy import (
    DEDUP_HINT_DUPLICATE_REASON_CODE,
    grammar_candidate_sort_key,
    normalize_dedup_hint,
    scoped_dedup_key,
    validate_dedup_hint,
)

from .automatic_layer_policy import (
    SemanticFenceError,
    is_semantic_fence_failure_code,
    validate_automatic_job_semantic_fence,
)
from .job_bootstrap import (
    GRAMMAR_BATCH_JOB_TYPE,
    GRAMMAR_BATCH_TARGET_SCOPE,
    GRAMMAR_JOB_TYPE,
    GRAMMAR_OPERATION_FINGERPRINT,
    GRAMMAR_TARGET_SCOPE,
    _fingerprint_matches_base,
)
from .job_runtime import (
    CapturedResumeClaim,
    ClaimResult,
    FenceViolationError,
    IllegalTransitionError,
    LeaseExpiredError,
    LeaseTokenMismatchError,
    ReaderJobRuntime,
    mark_reader_run_running,
    mark_reader_run_status,
)
from .layer_publisher import (
    GrammarBundleLayerPublisher,
    PublishedGrammarBatch,
    PublishedGrammarBundle,
)
from .lease_heartbeat import LeaseHeartbeat
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

logger = logging.getLogger(__name__)

# R7-3: grammar batch lease heartbeat configuration. A batch
# generate_batch + publish cycle routinely exceeds the 120s claim
# lease, so the worker renews the lease in the background every
# heartbeat interval for the WHOLE generate → publish phase. The
# interval is strictly shorter than the lease (and the shared
# LeaseHeartbeat manager defaults a missing interval to lease/4);
# the fix is continuous renewal, never a longer lease.
DEFAULT_GRAMMAR_BATCH_LEASE_DURATION = timedelta(seconds=120)
DEFAULT_GRAMMAR_BATCH_HEARTBEAT_INTERVAL = timedelta(seconds=30)

# R7-3: usage status labels for grammar batch model invocations.
# Every model call that actually completes (returns, with or without
# usage_data) is persisted exactly once with one of these statuses so
# token consumption is recorded even when the attempt never publishes:
#   - layer_published:    model completed AND the batch published;
#   - publication_failed: model completed but publish/fence failed;
#   - ownership_lost:     model completed but the lease was already
#                         invalid (heartbeat lost) so publish was
#                         skipped.
# Every such event also carries metadata_json.model_call_completed=True
# and the journal attempt/execution-slot identity so retries are
# distinguishable without coupling provider identity to a recovery lease. Model
# failures BEFORE any usage is produced keep the pre-existing
# STATUS_FAILED error event with usage_data=None (never fabricated
# tokens).
GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED = "layer_published"
GRAMMAR_USAGE_STATUS_PUBLICATION_FAILED = "publication_failed"
GRAMMAR_USAGE_STATUS_OWNERSHIP_LOST = "ownership_lost"
# R7-3b: the invocation usage row is written with this status the
# moment the model call returns (real tokens, outcome not yet known),
# then the SAME row is updated to one of the terminal statuses above.
# ``publication_interrupted`` covers cancellation between the persist
# and the publication outcome update.
GRAMMAR_USAGE_STATUS_MODEL_CALL_COMPLETED = "model_call_completed"
GRAMMAR_USAGE_STATUS_PUBLICATION_INTERRUPTED = "publication_interrupted"

# R7-3b: strong references for detached usage-outcome finalization
# tasks spawned from cancelled publish attempts (done-callback
# discards). Prevents GC of fire-and-forget outcome updates.
_DETACHED_USAGE_FINALIZATION_TASKS: set[asyncio.Task] = set()
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
MAX_GRAMMAR_DEDUP_HINT_LENGTH = 120
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
            "禁止 raw HTML 和 Markdown 标题（# / ## / ###）。"
            "讲清当前形式、句中作用及最有价值的可选发散；术语须配白话解释；"
            "不要固定「结构/规则/考点/例子」模板，不要用固定句数压力。"
            "禁止「高考中常考」「高考常见的...考点」「这是...考点」等总结性考试话术。"
            "前端会把 Markdown 反序列化为 Plate children 渲染。"
        ),
    )
    # P1-2 self-rating contract: three required fields consumed by all
    # three paths (per-unit / batch / window). ``quality_score`` drives
    # the primary sort (desc), ``reading_blocker`` breaks ties by
    # promoting blocker=true, and ``dedup_hint`` is the canonical
    # cross-type dedup key (normalized before comparison). All three
    # fields are required + range-checked so the LLM cannot emit a
    # bare candidate that degrades sorting to LLM-returned order.
    quality_score: int = Field(ge=1, le=5)
    reading_blocker: bool
    dedup_hint: str = Field(min_length=1, max_length=MAX_GRAMMAR_DEDUP_HINT_LENGTH)

    @field_validator("dedup_hint")
    @classmethod
    def _validate_and_normalize_dedup_hint(cls, value: str) -> str:
        # reader-grammar-candidate-selection: trim + normalize + 非空/≤120
        # 校验在 schema boundary 完成，返回 normalized hint 供下游
        # scoped_dedup_key 直接使用（idempotent）。
        return validate_dedup_hint(value)


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
            "禁止 raw HTML 和 Markdown 标题（# / ## / ###）。"
            "chunks 负责结构地图；analysis 负责主干定位、阅读顺序、修饰关系与"
            "理解障碍，禁止逐块复述 chunks 或复述整句翻译。"
            "前端会把 Markdown 反序列化为 Plate children 渲染。"
        ),
    )
    chunks: list[SentenceAnalysisChunkCandidate] = Field(
        min_length=1,
        max_length=MAX_GRAMMAR_CHUNKS_PER_ANALYSIS,
    )
    # P1-2 self-rating contract: three required fields consumed by all
    # three paths (per-unit / batch / window). See ``GrammarNoteCandidateItem``
    # for the full rationale. ``quality_score`` / ``reading_blocker`` /
    # ``dedup_hint`` must be consumed together — metadata-only validation
    # is not allowed.
    quality_score: int = Field(ge=1, le=5)
    reading_blocker: bool
    dedup_hint: str = Field(min_length=1, max_length=MAX_GRAMMAR_DEDUP_HINT_LENGTH)

    @field_validator("dedup_hint")
    @classmethod
    def _validate_and_normalize_dedup_hint(cls, value: str) -> str:
        # reader-grammar-candidate-selection: trim + normalize + 非空/≤120
        # 校验在 schema boundary 完成，返回 normalized hint 供下游
        # scoped_dedup_key 直接使用（idempotent）。
        return validate_dedup_hint(value)


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
        return await run_reader_scoped_agent(agent, prompt)

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
    # R7-3: True when the lease heartbeat reported ownership loss
    # (lease expired / token mismatch / job no longer claimed). In
    # that case the worker skips publish and job transitions — the
    # stale-lease recovery owns the job state — and the completed
    # model invocation's usage is recorded with status
    # GRAMMAR_USAGE_STATUS_OWNERSHIP_LOST. ``status`` remains a value
    # the pipeline runner already understands ("retry_later": the
    # recovery requeues the job).
    ownership_lost: bool = False


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
        return await run_reader_scoped_agent(agent, prompt)

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


def _failed_grammar_usage_attrs(
    execution: GrammarExecutionResult | None,
    error: GrammarExecutionError | None = None,
) -> dict[str, Any]:
    """Extract the invocation's usage attributes for a failed usage event.

    When the executor already returned (provider called and usage_data may
    be present) the event carries the real usage payload and the model
    identity; otherwise the typed error's model identity is used when
    available and no tokens are fabricated.
    """
    if execution is not None:
        return {
            "prompt_version": execution.prompt_version,
            "model_route": execution.model_route,
            "model_profile": execution.model_profile,
            "model_provider": execution.model_provider,
            "model_name": execution.model_name,
            "usage_data": execution.usage_data,
        }
    if error is not None:
        return {
            "prompt_version": error.prompt_version,
            "model_route": error.model_route,
            "model_profile": error.model_profile,
            "model_provider": error.model_provider,
            "model_name": error.model_name,
        }
    return {}


class GrammarBundleWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        layer_publisher: GrammarBundleLayerPublisher | None = None,
        executor: GrammarBundleExecutor | None = None,
        batch_executor: GrammarBatchExecutor | None = None,
        journal_service: ModelExecutionJournalService | None = None,
        batch_lease_duration: timedelta = DEFAULT_GRAMMAR_BATCH_LEASE_DURATION,
        batch_heartbeat_interval: timedelta = DEFAULT_GRAMMAR_BATCH_HEARTBEAT_INTERVAL,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._layer_publisher = layer_publisher or GrammarBundleLayerPublisher(pool=pool)
        self._executor = executor or PydanticAIGrammarBundleExecutor()
        # T4.1c: compact grammar batch executor for SHORT_BATCH / STRUCTURED_BATCH.
        # Defaults to PydanticAIGrammarBatchExecutor (real LLM); tests inject
        # FakeGrammarBatchExecutor to avoid real LLM calls.
        self._batch_executor = batch_executor or PydanticAIGrammarBatchExecutor()
        self._journal_service = journal_service or ModelExecutionJournalService(
            pool=pool
        )
        # R7-3: lease renewal for the batch generate → publish phase.
        # When the claim caller provides its own lease_duration it is
        # used for renewals (see process_claimed_grammar_batch_job);
        # these constructor values are the fallback for direct
        # claim-based processing and tests.
        self._batch_lease_duration = batch_lease_duration
        self._batch_heartbeat_interval = batch_heartbeat_interval

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

    @with_execution_correlation(CAPABILITY_READER_GRAMMAR_BUNDLE)
    async def process_claimed_grammar_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_GRAMMAR_RETRY_DELAY,
    ) -> GrammarJobProcessResult:
        context: GrammarJobContext | None = None
        execution: GrammarExecutionResult | None = None

        # Phase 4: lease renewal for the per-unit generate → publish
        # phase. A generate call longer than the lease let
        # recover_stale_leases requeue the job and a parallel worker
        # re-process it; the shared LeaseHeartbeat renews the claim
        # for the whole model call + publish phase. The publisher's
        # in-transaction fence remains the authoritative ownership
        # check.
        heartbeat = LeaseHeartbeat(
            job_runtime=self._job_runtime,
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            lease_duration=self._batch_lease_duration,
            heartbeat_interval=self._batch_heartbeat_interval,
        )

        try:
            context = await self._load_job_context(claim.job_id)
            await heartbeat.start()
            try:
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
                # R7-3b: actively probe ownership before publishing so
                # a lease lost during generation aborts the attempt
                # without publishing. The publisher's in-transaction
                # fence is still the final authoritative check.
                await heartbeat.verify_ownership()
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
            finally:
                # Cleanup on success, exception AND external
                # cancellation: the renewal task never survives this
                # method. Renewal failures stay visible via
                # heartbeat.lost (logged in the loop); stop() itself
                # does not raise.
                await heartbeat.stop()
        except FenceViolationError:
            await end_worker_span_fence_violation()
            # The model call completed (tokens spent) but the publish
            # fence failed — record the invocation's usage so the
            # usage table reflects real model consumption. The
            # invocation key namespaces this row within the per-unit
            # path (distinct from the batch path's
            # reader_grammar_batch: namespace).
            await self._record_failed_usage_event(
                context=context,
                error_code="publish_fence_failed",
                error_message="grammar unit publish fence failed",
                prompt_version=(execution.prompt_version if execution else None),
                model_route=(execution.model_route if execution else GRAMMAR_MODEL_ROUTE),
                model_profile=(execution.model_profile if execution else None),
                model_provider=(execution.model_provider if execution else None),
                model_name=(execution.model_name if execution else None),
                usage_data=(execution.usage_data if execution else None),
                invocation_key=self._per_unit_invocation_key(claim),
            )
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
            if is_semantic_fence_failure_code(exc.failure_code):
                await self._job_runtime.transition(
                    job_id=claim.job_id,
                    target_status="superseded",
                    lease_token=claim.lease_token,
                    rationale_code=exc.failure_code,
                )
                await self._mark_run_status(
                    claim.run_id,
                    status="superseded",
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    finished_at=datetime.now(UTC),
                )
                await end_worker_span_execution_error(
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                )
                return GrammarJobProcessResult(
                    claim=claim,
                    context=context,
                    status="superseded",
                )
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
                    **_failed_grammar_usage_attrs(execution, exc),
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
                **_failed_grammar_usage_attrs(execution, exc),
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
                **_failed_grammar_usage_attrs(execution),
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
                       unit.text_hash,
                       unit.metadata_json
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

            try:
                unit_meta = row["metadata_json"]
                if hasattr(unit_meta, "keys"):
                    unit_meta = dict(unit_meta)
                elif not isinstance(unit_meta, dict):
                    unit_meta = {}
                validate_automatic_job_semantic_fence(
                    job_input=row["input_json"]
                    if isinstance(row["input_json"], dict)
                    else {},
                    layer="grammar_note",
                    layers_any=("grammar_note", "sentence_analysis"),
                    unit_metadata_list=[unit_meta],
                    operation_fingerprint=str(row["operation_fingerprint"]),
                    trusted_record_id=str(row["reading_record_id"]),
                    trusted_base_id=str(row["base_id"]),
                    trusted_generation=int(row["expected_generation"]),
                )
            except SemanticFenceError as exc:
                raise GrammarExecutionError(
                    str(exc),
                    retryable=False,
                    failure_class="validation",
                    failure_code=exc.code,
                ) from exc

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
            await mark_reader_run_running(conn, run_id)

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
            await mark_reader_run_status(
                conn,
                run_id,
                status=status,
                failure_class=failure_class,
                failure_code=failure_code,
                finished_at=finished_at,
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
        usage_data: dict | None = None,
        invocation_key: str | None = None,
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
                request_id=invocation_key,
                workflow_name="reader_orchestration",
                workflow_version=GRAMMAR_WORKFLOW_VERSION,
                prompt_version=prompt_version,
                model_route=model_route,
                model_profile_id=model_profile,
                model_profile=model_profile,
                model_provider=model_provider,
                model_name=model_name,
                planner_kind="llm_worker",
                usage_data=usage_data,
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
        """Claim and process the next grammar batch job for the record.

        R7-3: ``lease_duration`` (the same value used for the claim)
        is forwarded to the heartbeat so renewals extend the lease by
        exactly the claimed duration.
        """
        resume_job_id = await self._find_captured_grammar_batch_resume_job_id(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if resume_job_id is not None:
            resume = await self._job_runtime.claim_captured_resume(
                job_id=resume_job_id,
                lease_owner=lease_owner,
                lease_duration=lease_duration,
            )
            if resume is not None:
                await self._mark_run_running(resume.claim.run_id)
                return await self._process_captured_grammar_batch_resume(
                    resume=resume,
                    lease_duration=lease_duration,
                )

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
            lease_duration=lease_duration,
        )

    async def _find_captured_grammar_batch_resume_job_id(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
    ) -> UUID | None:
        async with self.get_pool().acquire() as conn:
            return await conn.fetchval(
                """
                SELECT job.id
                FROM reader_jobs job
                WHERE job.reading_record_id = $1
                  AND job.base_id = $2
                  AND job.expected_generation = $3
                  AND job.job_type = 'build_grammar_bundle'
                  AND job.target_type = 'unit_range'
                  AND job.status = 'paused'
                  AND job.pause_owner = 'system'
                  AND job.rationale_code =
                      'model_execution_captured_resume_required'
                  AND job.failure_class = 'model_execution'
                  AND job.failure_code = 'post_provider_resume_required'
                  AND EXISTS (
                      SELECT 1
                      FROM ai_model_execution_journal journal
                      WHERE journal.reader_job_id = job.id
                        AND journal.attempt_ordinal = job.attempt_count
                        AND journal.capture_state = 'captured'
                  )
                ORDER BY job.created_at ASC, job.id ASC
                LIMIT 1
                """,
                record_id,
                base_id,
                expected_generation,
            )

    @with_execution_correlation(CAPABILITY_READER_GRAMMAR_BUNDLE)
    async def process_claimed_grammar_batch_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_GRAMMAR_RETRY_DELAY,
        lease_duration: timedelta | None = None,
    ) -> GrammarBatchJobProcessResult:
        """Run the batch LLM call and publish N per-unit grammar layers.

        R7-3 + R7-3b contracts:

        Heartbeat: a shared :class:`LeaseHeartbeat` renews the claim
        lease from BEFORE ``generate_batch`` until AFTER publish (or
        any exit path). Once the heartbeat reports ownership loss
        (lease expired / token mismatch / job no longer claimed), this
        attempt does NOT publish and writes NOTHING to ``reader_jobs``
        or ``reader_runs`` — those updates are unfenced by run_id and
        could clobber a new attempt's state; the stale-lease recovery
        / current owner decides subsequent state. The publisher's
        in-transaction claim/fence validation remains the
        authoritative ownership check.

        Journal + usage (exactly-once per real model invocation):

            persist STARTED before provider
            → generate_batch returns
            → persist the versioned result + usage draft as CAPTURED/PENDING
              under ``reader:{capability}:{job}:{attempt}:{slot}``
            → DB-only materializer best-effort reconciles the unique usage event
            → check ownership
            → publish
            → UPDATE THE SAME usage row with the publication outcome
              (layer_published / publication_failed / ownership_lost /
              publication_interrupted).

        The publication outcome never inserts a second usage event.
        A retried persistence of the same invocation reuses the envelope
        hash and never duplicates tokens; a new attempt ordinal has its own
        invocation and event. Recovery uses the captured receipt with a new
        lease but never calls the provider again. Model failures
        BEFORE any usage is returned keep the pre-existing
        ``STATUS_FAILED`` error event with ``usage_data=None`` (never
        fabricated tokens). Cancellation between the persist and the
        outcome update flips the existing row to
        ``publication_interrupted`` from a detached task.

        Other exception handling mirrors ``process_claimed_grammar_job``:
        ``FenceViolationError`` → ``superseded`` (while ownership is
        still held); ``GrammarExecutionError`` (retryable) →
        ``retry_later``; ``GrammarExecutionError`` (non-retryable) →
        ``failed_terminal``; any other ``Exception`` →
        ``failed_terminal``. When ownership was lost the result is
        ``retry_later`` with ``ownership_lost=True`` instead.
        """
        context: GrammarBatchJobContext | None = None
        execution: GrammarBatchExecutionResult | None = None
        published_batch: PublishedGrammarBatch | None = None

        heartbeat = LeaseHeartbeat(
            job_runtime=self._job_runtime,
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            lease_duration=lease_duration or self._batch_lease_duration,
            heartbeat_interval=self._batch_heartbeat_interval,
        )

        # --- context loading (pre-model; failures are not invocations) ---
        try:
            context = await self._load_batch_job_context(claim.job_id)
        except GrammarExecutionError as exc:
            if is_semantic_fence_failure_code(exc.failure_code):
                await self._job_runtime.transition(
                    job_id=claim.job_id,
                    target_status="superseded",
                    lease_token=claim.lease_token,
                    rationale_code=exc.failure_code,
                )
                await self._mark_run_status(
                    claim.run_id,
                    status="superseded",
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    finished_at=datetime.now(UTC),
                )
                await end_worker_span_execution_error(
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                )
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="superseded",
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

        # --- model call → usage persist → ownership → publish ---
        await heartbeat.start()
        try:
            identity = self._batch_execution_identity(claim)
            try:
                begin = await self._journal_service.begin_execution(
                    identity=identity,
                    invocation_kind="reader.grammar_batch",
                )
            except Exception as exc:
                await self._pause_model_execution_claim(
                    claim,
                    rationale_code="model_execution_begin_unconfirmed",
                    failure_code="journal_begin_failed",
                    failure_message=str(exc),
                )
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="paused",
                )
            if not begin.provider_call_allowed:
                await self._pause_model_execution_claim(
                    claim,
                    rationale_code=(
                        "model_execution_captured_resume_required"
                        if begin.capture_state == "captured"
                        else "model_execution_ambiguous"
                    ),
                    failure_code=(
                        "post_provider_resume_required"
                        if begin.capture_state == "captured"
                        else "provider_outcome_ambiguous"
                    ),
                )
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="paused",
                )
            try:
                execution = await self._batch_executor.generate_batch(context)
            except GrammarExecutionError as exc:
                # Model failed BEFORE returning usage: pre-existing
                # STATUS_FAILED error event, usage_data=None (never
                # fabricated tokens).
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
                if heartbeat.lost:
                    # R7-3b: ownership already invalid — NO writes to
                    # reader_jobs / reader_runs from this attempt.
                    return GrammarBatchJobProcessResult(
                        claim=claim,
                        context=context,
                        status="retry_later",
                        ownership_lost=True,
                    )
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
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="failed_terminal",
                )

            # R7-3b: the model call really completed → persist its
            # usage NOW (status=model_call_completed), idempotently by
            # invocation key, BEFORE the ownership check and publish.
            try:
                usage_event_id = (
                    await self._persist_batch_invocation_usage_cancel_safe(
                        context=context,
                        execution=execution,
                        claim=claim,
                    )
                )
            except CaptureEnvelopeConflictError as exc:
                try:
                    await self._pause_model_execution_claim(
                        claim,
                        rationale_code="model_execution_capture_conflict",
                        failure_code="capture_envelope_conflict",
                        failure_message=str(exc),
                    )
                except (
                    FenceViolationError,
                    IllegalTransitionError,
                    LeaseExpiredError,
                    LeaseTokenMismatchError,
                    LookupError,
                ):
                    return GrammarBatchJobProcessResult(
                        claim=claim,
                        context=context,
                        status="retry_later",
                        ownership_lost=True,
                    )
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="paused",
                )
            except Exception as exc:
                await self._pause_model_execution_claim(
                    claim,
                    rationale_code="model_execution_ambiguous",
                    failure_code="provider_outcome_ambiguous",
                    failure_message=str(exc),
                )
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="paused",
                    usage_data=execution.usage_data,
                    prompt_version=execution.prompt_version,
                    model_route=execution.model_route,
                    model_profile=execution.model_profile,
                    model_provider=execution.model_provider,
                    model_name=execution.model_name,
                )
            # Ownership gate BEFORE publish (R7-3b): actively probe
            # the lease — catches both loop-detected loss AND leases
            # that expired since the last renewal (e.g. stalled or
            # neutered renewals). On invalid ownership: finalize the
            # usage row as ownership_lost and bail without publishing,
            # with NO writes to reader_jobs / reader_runs (unfenced
            # updates could clobber the new owner's state; the
            # recovery / current owner decides subsequent state).
            try:
                await heartbeat.verify_ownership()
            except (
                FenceViolationError,
                IllegalTransitionError,
                LookupError,
            ) as exc:
                await self._finalize_batch_usage_outcome(
                    usage_event_id,
                    GRAMMAR_USAGE_STATUS_OWNERSHIP_LOST,
                    error_code="heartbeat_lost",
                    error_message=str(exc),
                )
                await end_worker_span_execution_error(
                    failure_class="lease",
                    failure_code="heartbeat_lost",
                )
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="retry_later",
                    ownership_lost=True,
                )

            try:
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
            except asyncio.CancelledError:
                # The invocation usage row exists; flip its outcome
                # from a DETACHED task — awaiting here would re-raise
                # CancelledError and strand the row at
                # model_call_completed.
                self._spawn_detached_usage_finalization(
                    usage_event_id,
                    GRAMMAR_USAGE_STATUS_PUBLICATION_INTERRUPTED,
                    error_code="cancelled_during_publish",
                )
                raise
            except FenceViolationError:
                await self._finalize_batch_usage_outcome(
                    usage_event_id,
                    GRAMMAR_USAGE_STATUS_PUBLICATION_FAILED,
                    error_code="publish_fence_failed",
                    error_message="grammar batch publish fence failed",
                )
                await end_worker_span_fence_violation()
                if not heartbeat.lost:
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
            except Exception as exc:
                await self._finalize_batch_usage_outcome(
                    usage_event_id,
                    GRAMMAR_USAGE_STATUS_PUBLICATION_FAILED,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
                await end_worker_span_generic_exception(
                    layer="grammar_bundle", exc=exc
                )
                if heartbeat.lost:
                    return GrammarBatchJobProcessResult(
                        claim=claim,
                        context=context,
                        status="retry_later",
                        ownership_lost=True,
                    )
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
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="failed_terminal",
                )

            # Success: update THE SAME usage row with the terminal
            # outcome (never a second event).
            await self._finalize_batch_usage_outcome(
                usage_event_id,
                GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED,
                published_batch=published_batch,
            )
            await end_worker_span_success(
                ai_usage_event_id=usage_event_id,
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
        finally:
            # Cleanup on success, exception AND external cancellation:
            # the renewal task never survives this method. Renewal
            # failures stay visible via heartbeat.lost (logged in the
            # loop); stop() itself does not raise.
            await heartbeat.stop()

    async def _process_captured_grammar_batch_resume(
        self,
        *,
        resume: CapturedResumeClaim,
        lease_duration: timedelta,
    ) -> GrammarBatchJobProcessResult:
        """Publish a captured batch receipt without granting provider capability."""
        claim = resume.claim
        context: GrammarBatchJobContext | None = None
        try:
            if len(resume.receipts) != 1:
                raise PayloadContractError("grammar_batch_receipt_count_invalid")
            receipt = resume.receipts[0]
            execution = self._execution_from_captured_receipt(receipt)
            context = await self._load_batch_job_context(claim.job_id)
        except Exception as exc:
            await self._pause_model_execution_claim(
                claim,
                rationale_code="model_execution_receipt_invalid",
                failure_code="receipt_payload_invalid",
                failure_message=str(exc),
            )
            return GrammarBatchJobProcessResult(
                claim=claim,
                context=context,
                status="paused",
            )

        heartbeat = LeaseHeartbeat(
            job_runtime=self._job_runtime,
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            lease_duration=lease_duration,
            heartbeat_interval=self._batch_heartbeat_interval,
        )
        await heartbeat.start()
        try:
            usage_event_id = receipt.ai_usage_event_id
            try:
                await self._journal_service.materialize_pending()
                materialized_receipt = (
                    await self._journal_service.load_captured_receipt(
                        invocation_key=receipt.identity.invocation_key
                    )
                )
                usage_event_id = materialized_receipt.ai_usage_event_id
            except Exception as exc:
                logger.warning(
                    "grammar_batch_usage_delivery_deferred: invocation_key=%s "
                    "error=%s",
                    receipt.identity.invocation_key,
                    type(exc).__name__,
                )

            try:
                await heartbeat.verify_ownership()
            except (
                FenceViolationError,
                IllegalTransitionError,
                LookupError,
            ) as exc:
                await self._finalize_batch_usage_outcome(
                    usage_event_id,
                    GRAMMAR_USAGE_STATUS_OWNERSHIP_LOST,
                    error_code="heartbeat_lost",
                    error_message=str(exc),
                )
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="retry_later",
                    ownership_lost=True,
                )

            try:
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
            except asyncio.CancelledError:
                self._spawn_detached_usage_finalization(
                    usage_event_id,
                    GRAMMAR_USAGE_STATUS_PUBLICATION_INTERRUPTED,
                    error_code="cancelled_during_resume_publish",
                )
                raise
            except FenceViolationError:
                await self._finalize_batch_usage_outcome(
                    usage_event_id,
                    GRAMMAR_USAGE_STATUS_PUBLICATION_FAILED,
                    error_code="publish_fence_failed",
                    error_message="grammar batch resume publish fence failed",
                )
                if not heartbeat.lost:
                    await self._job_runtime.transition(
                        job_id=claim.job_id,
                        target_status="superseded",
                        lease_token=claim.lease_token,
                        rationale_code="publish_fence_failed",
                    )
                raise
            except Exception as exc:
                await self._finalize_batch_usage_outcome(
                    usage_event_id,
                    GRAMMAR_USAGE_STATUS_PUBLICATION_FAILED,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
                if heartbeat.lost:
                    return GrammarBatchJobProcessResult(
                        claim=claim,
                        context=context,
                        status="retry_later",
                        ownership_lost=True,
                    )
                await self._pause_model_execution_claim(
                    claim,
                    rationale_code="model_execution_captured_resume_required",
                    failure_code="post_provider_resume_required",
                    failure_message=str(exc),
                )
                return GrammarBatchJobProcessResult(
                    claim=claim,
                    context=context,
                    status="paused",
                )

            await self._finalize_batch_usage_outcome(
                usage_event_id,
                GRAMMAR_USAGE_STATUS_LAYER_PUBLISHED,
                published_batch=published_batch,
            )
            await end_worker_span_success(
                ai_usage_event_id=usage_event_id,
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
        finally:
            await heartbeat.stop()

    @staticmethod
    def _execution_from_captured_receipt(
        receipt: CapturedReceipt,
    ) -> GrammarBatchExecutionResult:
        if receipt.invocation_kind != "reader.grammar_batch":
            raise PayloadContractError("grammar_batch_invocation_kind_invalid")
        payload = decode_resume_payload(
            kind=receipt.resume_payload_kind,
            schema_version=receipt.resume_payload_schema_version,
            payload=receipt.normalized_payload,
        )
        usage = decode_usage_event_draft(
            schema_version=receipt.usage_event_draft_schema_version,
            payload=receipt.usage_event_draft,
        )
        return GrammarBatchExecutionResult(
            outputs=[(item.unit_id, item.output) for item in payload.outputs],
            usage_data=usage.usage_data,
            prompt_version=usage.prompt_version,
            model_route=usage.model_route,
            model_profile=usage.model_profile,
            model_provider=usage.model_provider,
            model_name=usage.model_name,
            diagnostics=payload.diagnostics,
        )

    async def _pause_model_execution_claim(
        self,
        claim: ClaimResult,
        *,
        rationale_code: str,
        failure_code: str,
        failure_message: str | None = None,
    ) -> None:
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status="paused",
            lease_token=claim.lease_token,
            pause_owner="system",
            failure_class="model_execution",
            failure_code=failure_code,
            failure_message=failure_message,
            rationale_code=rationale_code,
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
            batch_meta_list: list[dict[str, Any]] = []
            for unit_id in target_unit_ids:
                unit_row = await conn.fetchrow(
                    """
                    SELECT unit_id, order_index,
                           base_start_utf16, base_end_utf16, text_hash,
                           metadata_json
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
                um = unit_row["metadata_json"]
                if hasattr(um, "keys"):
                    um = dict(um)
                elif not isinstance(um, dict):
                    um = {}
                batch_meta_list.append(um)
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

        try:
            validate_automatic_job_semantic_fence(
                job_input=input_json if isinstance(input_json, dict) else {},
                layer="grammar_note",
                layers_any=("grammar_note", "sentence_analysis"),
                unit_metadata_list=batch_meta_list,
                operation_fingerprint=str(job_row["operation_fingerprint"]),
                trusted_record_id=str(job_row["reading_record_id"]),
                trusted_base_id=str(job_row["base_id"]),
                trusted_generation=int(job_row["expected_generation"]),
            )
        except SemanticFenceError as exc:
            raise GrammarExecutionError(
                str(exc),
                retryable=False,
                failure_class="validation",
                failure_code=exc.code,
            ) from exc

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

    @staticmethod
    def _batch_execution_identity(claim: ClaimResult) -> ExecutionIdentity:
        execution_slot = 1
        return ExecutionIdentity(
            invocation_key=(
                f"reader:{CAPABILITY_READER_GRAMMAR_BUNDLE}:"
                f"{claim.job_id}:{claim.attempt_count}:{execution_slot}"
            ),
            reader_job_id=claim.job_id,
            reader_run_id=claim.run_id,
            attempt_ordinal=claim.attempt_count,
            execution_slot=execution_slot,
        )

    @staticmethod
    def _batch_invocation_key(claim: ClaimResult) -> str:
        """Legacy request_id key retained until physical accounting deletion.

        Carried in ``ai_usage_events.request_id`` within the reserved
        ``reader_grammar_batch:`` namespace (DB backstop: migration
        0022 unique partial index). A retried job attempt is claimed
        with a NEW lease token → a new key → a genuinely new
        invocation event. A retried PERSISTENCE of the same attempt
        reuses this key and never produces a second row.
        """
        return f"reader_grammar_batch:{claim.job_id}:{claim.lease_token}"

    @staticmethod
    def _per_unit_invocation_key(claim: ClaimResult) -> str:
        """Phase 4: stable idempotency key for one per-unit model invocation.

        Carried in ``ai_usage_events.request_id`` within the reserved
        ``reader_grammar_per_unit:`` namespace (distinct from the batch
        path's ``reader_grammar_batch:`` namespace). The per-unit path
        is not covered by migration 0022's partial unique index; the
        key still carries the same job_id + lease_token identity so a
        retried job attempt (new lease token) gets a genuinely new
        invocation event. A retried persistence of the same attempt
        reuses this key.
        """
        return f"reader_grammar_per_unit:{claim.job_id}:{claim.lease_token}"

    async def _persist_batch_invocation_usage(
        self,
        *,
        context: GrammarBatchJobContext,
        execution: GrammarBatchExecutionResult,
        claim: ClaimResult,
    ) -> UUID | None:
        """Capture the typed result and usage draft before any publish."""
        identity = self._batch_execution_identity(claim)
        prepared = prepare_capture_envelope(
            invocation_kind="reader.grammar_batch",
            resume_payload_kind="reader.grammar_batch.result",
            resume_payload_schema_version=1,
            usage_event_draft_schema_version=1,
            normalized_payload={
                "outputs": [
                    {
                        "unit_id": unit_id,
                        "output": output.model_dump(mode="json"),
                    }
                    for unit_id, output in execution.outputs
                ],
                "diagnostics": execution.diagnostics,
            },
            usage_event_draft={
                "usage_scope": USAGE_SCOPE_SYSTEM_INTERNAL,
                "capability_code": CAPABILITY_READER_GRAMMAR_BUNDLE,
                "billing_mode": BILLING_MODE_INTERNAL_ONLY,
                "status": GRAMMAR_USAGE_STATUS_MODEL_CALL_COMPLETED,
                "user_id": context.user_id,
                "reading_record_id": context.reading_record_id,
                "reader_run_id": context.run_id,
                "reader_job_id": context.job_id,
                "workflow_name": "reader_orchestration",
                "workflow_version": GRAMMAR_WORKFLOW_VERSION,
                "prompt_version": execution.prompt_version,
                "model_route": execution.model_route,
                "model_profile_id": execution.model_profile,
                "model_profile": execution.model_profile,
                "model_provider": execution.model_provider,
                "model_name": execution.model_name,
                "planner_kind": "llm_worker",
                "usage_data": execution.usage_data,
                "operation_fingerprint": context.operation_fingerprint,
                "metadata_json": {
                    "base_id": str(context.base_id),
                    "unit_count": len(context.units),
                    "source_language": context.source_language,
                    "model_call_completed": True,
                    "invocation_key": identity.invocation_key,
                    "attempt_ordinal": identity.attempt_ordinal,
                    "execution_slot": identity.execution_slot,
                },
            },
        )
        receipt = await self._journal_service.capture_execution(
            identity=identity,
            prepared=prepared,
        )
        try:
            await self._journal_service.materialize_pending()
            receipt = await self._journal_service.load_captured_receipt(
                invocation_key=receipt.identity.invocation_key
            )
        except Exception as exc:
            logger.warning(
                "grammar_batch_usage_delivery_deferred: invocation_key=%s "
                "error=%s",
                receipt.identity.invocation_key,
                type(exc).__name__,
            )
        return receipt.ai_usage_event_id


    async def _persist_batch_invocation_usage_cancel_safe(
        self,
        *,
        context: GrammarBatchJobContext,
        execution: GrammarBatchExecutionResult,
        claim: ClaimResult,
    ) -> UUID | None:
        """Keep CAPTURED/PENDING + materialization alive on cancellation."""
        persistence_task = asyncio.create_task(
            self._persist_batch_invocation_usage(
                context=context,
                execution=execution,
                claim=claim,
            ),
            name=f"grammar-batch-usage-persist-{claim.job_id}",
        )
        try:
            return await asyncio.shield(persistence_task)
        except asyncio.CancelledError:
            self._spawn_detached_usage_persistence_finalization(
                persistence_task,
                claim=claim,
            )
            raise

    def _spawn_detached_usage_persistence_finalization(
        self,
        persistence_task: asyncio.Task[UUID | None],
        *,
        claim: ClaimResult,
    ) -> None:
        """Finish persistence and mark interruption after caller cancellation."""

        async def _finish() -> None:
            try:
                event_id = await persistence_task
                await self._finalize_batch_usage_outcome(
                    event_id,
                    GRAMMAR_USAGE_STATUS_PUBLICATION_INTERRUPTED,
                    error_code="cancelled_during_usage_persistence",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "detached grammar batch usage persistence failed for job %s",
                    claim.job_id,
                )

        task = asyncio.create_task(
            _finish(),
            name=f"grammar-batch-usage-persist-finalize-{claim.job_id}",
        )
        _DETACHED_USAGE_FINALIZATION_TASKS.add(task)
        task.add_done_callback(_DETACHED_USAGE_FINALIZATION_TASKS.discard)

    async def _finalize_batch_usage_outcome(
        self,
        event_id: UUID | None,
        status: str,
        *,
        published_batch: PublishedGrammarBatch | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update the same usage row, retrying transient outcome failures."""
        if event_id is None:
            logger.warning(
                "grammar batch usage outcome %r not recorded: the "
                "invocation usage persistence was never confirmed",
                status,
            )
            return
        metadata_patch: dict[str, Any] = {"publication_status": status}
        if published_batch is not None:
            metadata_patch["published_layer_ids"] = list(
                published_batch.layer_ids
            )
            metadata_patch["published_layer_types"] = list(
                published_batch.layer_types
            )
            metadata_patch["no_op"] = bool(published_batch.no_op)

        for attempt in range(3):
            updated = await update_ai_usage_event_outcome(
                event_id,
                status=status,
                metadata_patch=metadata_patch,
                error_code=error_code,
                error_message=error_message,
            )
            if updated:
                return
            if attempt < 2:
                await asyncio.sleep(0.05 * (attempt + 1))

        logger.warning(
            "grammar_batch_usage_outcome_unconfirmed: event_id=%s status=%s",
            event_id,
            status,
        )

    def _spawn_detached_usage_finalization(
        self,
        event_id: UUID | None,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """R7-3b: cancel-safe outcome update. Runs the outcome update
        as a DETACHED task so a cancellation during publish still
        records ``publication_interrupted`` on the existing usage row
        (awaiting inside the cancelled attempt would re-raise
        CancelledError and strand the row)."""
        if event_id is None:
            return
        task = asyncio.create_task(
            self._finalize_batch_usage_outcome(
                event_id,
                status,
                error_code=error_code,
                error_message=error_message,
            ),
            name=f"grammar-batch-usage-finalize-{event_id}",
        )
        _DETACHED_USAGE_FINALIZATION_TASKS.add(task)
        task.add_done_callback(_DETACHED_USAGE_FINALIZATION_TASKS.discard)

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

    # P1-2 self-rating contract: merge grammar_notes + sentence_analyses
    # into a single ordered stream using the unified sort key
    # (quality_score desc → reading_blocker=true first → grammar_note on
    # tie). reader-grammar-candidate-selection: scoped dedup uses
    # (anchor_segment_id, normalized dedup_hint) tuple; same anchor +
    # same hint is rejected (winner decided by sort order); different
    # anchor + same hint is allowed (full-text repetition control is
    # delegated to pattern/density/budget gates in window_selector).
    # Losers emit a `dedup_hint_duplicate` diagnostic so the rejection
    # is observable, never silent.
    unified_candidates: list[
        tuple[
            tuple[int, int, int],
            int,
            str,
            GrammarNoteCandidateItem | SentenceAnalysisCandidateItem,
        ]
    ] = []
    for idx, item in enumerate(candidate_output.grammar_notes):
        unified_candidates.append(
            (
                grammar_candidate_sort_key(
                    item_type="grammar_note",
                    quality_score=item.quality_score,
                    reading_blocker=item.reading_blocker,
                ),
                idx,
                "grammar_note",
                item,
            )
        )
    for idx, item in enumerate(candidate_output.sentence_analyses):
        unified_candidates.append(
            (
                grammar_candidate_sort_key(
                    item_type="sentence_analysis",
                    quality_score=item.quality_score,
                    reading_blocker=item.reading_blocker,
                ),
                idx,
                "sentence_analysis",
                item,
            )
        )

    unified_candidates.sort(key=lambda triple: (triple[0], triple[1]))

    # reader-grammar-candidate-selection: scoped dedup uses
    # (anchor_segment_id, normalized_dedup_hint) tuple. Same anchor +
    # same hint is rejected (winner decided by sort order); different
    # anchor + same hint is allowed (full-text repetition control is
    # delegated to pattern/density/budget gates in window_selector).
    seen_scoped_keys: set[tuple[str, str]] = set()
    # winner tracking: when a later candidate is rejected, record the
    # winner's (item_type, anchor_segment_id, item_index) so the
    # diagnostic payload can carry the full winner info.
    winner_info: dict[tuple[str, str], tuple[str, str, int]] = {}
    grammar_note_count = 0
    sentence_analysis_count = 0

    for _sort_key, original_idx, item_type, item in unified_candidates:
        normalized_hint = normalize_dedup_hint(item.dedup_hint)
        anchor_segment_id = (
            item.spans[0].anchor_segment_id
            if item_type == "grammar_note"
            else item.anchor_segment_id
        )
        scoped_key = scoped_dedup_key(
            anchor_segment_id=anchor_segment_id,
            dedup_hint=item.dedup_hint,
        )
        if scoped_key in seen_scoped_keys:
            # P1-2: scoped dedup loser. Emit a diagnostic so the
            # rejection is observable; never silently drop.
            selected_text = (
                item.spans[0].selected_text
                if item_type == "grammar_note"
                else item.selected_text
            )
            winner_t, winner_anchor, winner_idx = winner_info[scoped_key]
            skipped_items.append(
                _build_skip_diagnostic(
                    item_index=original_idx,
                    item_type=item_type,
                    anchor_segment_id=anchor_segment_id,
                    selected_text=selected_text,
                    reason_code=DEDUP_HINT_DUPLICATE_REASON_CODE,
                )
            )
            # ``_build_skip_diagnostic`` does not know about scoped-dedup
            # winner fields, so attach them here for auditability.
            skipped_items[-1].update(
                {
                    "normalized_hint": normalized_hint,
                    "winner_item_type": winner_t,
                    "winner_anchor_segment_id": winner_anchor,
                    "winner_item_index": winner_idx,
                }
            )
            continue

        if item_type == "grammar_note":
            if grammar_note_count >= MAX_GRAMMAR_NOTE_ITEMS:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
                        anchor_segment_id=item.spans[0].anchor_segment_id,
                        selected_text=item.spans[0].selected_text,
                        reason_code="per_unit_budget_exceeded",
                    )
                )
                continue

            resolved_spans: list[ReaderTextRangeAnchor] = []
            note_rejected = False
            for span_index, span in enumerate(item.spans):
                segment = segments_by_id.get(span.anchor_segment_id)
                if segment is not None and segment.segment_type == "fallback_window":
                    skipped_items.append(
                        _build_skip_diagnostic(
                            item_index=original_idx,
                            item_type=item_type,
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
                            item_index=original_idx,
                            item_type=item_type,
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
                seen_scoped_keys.add(scoped_key)
                winner_info[scoped_key] = (
                    item_type,
                    anchor_segment_id,
                    original_idx,
                )
                grammar_note_count += 1
            except ValidationError:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
                        anchor_segment_id=item.spans[0].anchor_segment_id,
                        selected_text=item.spans[0].selected_text,
                        reason_code="resolved_item_invalid",
                    )
                )
        else:  # sentence_analysis
            if sentence_analysis_count >= MAX_SENTENCE_ANALYSIS_ITEMS:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
                        anchor_segment_id=item.anchor_segment_id,
                        selected_text=item.selected_text,
                        reason_code="per_unit_budget_exceeded",
                    )
                )
                continue

            segment = segments_by_id.get(item.anchor_segment_id)
            if segment is not None and segment.segment_type == "fallback_window":
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
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
                        item_index=original_idx,
                        item_type=item_type,
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
                seen_scoped_keys.add(scoped_key)
                winner_info[scoped_key] = (
                    item_type,
                    anchor_segment_id,
                    original_idx,
                )
                sentence_analysis_count += 1
            except ValidationError:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
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

    P1-2 self-rating contract: within each unit, grammar_notes +
    sentence_analyses are merged into a single ordered stream using the
    unified sort key (quality_score desc → reading_blocker=true first →
    grammar_note on tie). reader-grammar-candidate-selection: scoped
    dedup uses ``(anchor_segment_id, normalized_dedup_hint)`` tuple
    scoped **per unit** — the same learning point in different units
    (or on different anchors within a unit) is allowed to surface
    independently, but within a unit the same anchor + same
    dedup_hint across grammar_note / sentence_analysis keeps only one
    candidate (winner decided by sort order); different anchor + same
    hint is allowed. Losers emit a ``dedup_hint_duplicate`` diagnostic.

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

    # P1-2: build a unified candidate stream tagged with original index
    # + item_type, then sort by (sort_key, original_index). Each candidate
    # is routed to its owning unit via anchor_segment_id. Per-unit dedup
    # + per-type budget is enforced in stream order.
    unified_candidates: list[
        tuple[
            tuple[int, int, int],
            int,
            str,
            GrammarNoteCandidateItem | SentenceAnalysisCandidateItem,
        ]
    ] = []
    for idx, item in enumerate(candidate_output.grammar_notes):
        unified_candidates.append(
            (
                grammar_candidate_sort_key(
                    item_type="grammar_note",
                    quality_score=item.quality_score,
                    reading_blocker=item.reading_blocker,
                ),
                idx,
                "grammar_note",
                item,
            )
        )
    for idx, item in enumerate(candidate_output.sentence_analyses):
        unified_candidates.append(
            (
                grammar_candidate_sort_key(
                    item_type="sentence_analysis",
                    quality_score=item.quality_score,
                    reading_blocker=item.reading_blocker,
                ),
                idx,
                "sentence_analysis",
                item,
            )
        )
    unified_candidates.sort(key=lambda triple: (triple[0], triple[1]))

    # Per-unit scoped dedup ledger. reader-grammar-candidate-selection:
    # scoped dedup key is (anchor_segment_id, normalized_dedup_hint).
    # Different units keep independent ledgers so the same learning point
    # can surface in multiple units; within a unit, different anchors
    # with the same hint are also allowed (only same anchor + same hint
    # is rejected, winner decided by sort order). ``unit_winner_info``
    # tracks the winning candidate so the diagnostic payload can carry
    # the full winner info.
    unit_seen_scoped_keys: dict[str, set[tuple[str, str]]] = {
        u.unit_id: set() for u in context.units
    }
    unit_winner_info: dict[str, dict[tuple[str, str], tuple[str, str, int]]] = {
        u.unit_id: {} for u in context.units
    }

    for _sort_key, original_idx, item_type, item in unified_candidates:
        if item_type == "grammar_note":
            first_span = item.spans[0] if item.spans else None
            if first_span is None:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
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
                        item_index=original_idx,
                        item_type=item_type,
                        anchor_segment_id=first_span.anchor_segment_id,
                        selected_text=first_span.selected_text,
                        reason_code="anchor_segment_unknown",
                    )
                )
                continue
            unit_context, _ = mapping

            scoped_key = scoped_dedup_key(
                anchor_segment_id=first_span.anchor_segment_id,
                dedup_hint=item.dedup_hint,
            )
            if scoped_key in unit_seen_scoped_keys[unit_context.unit_id]:
                winner_t, winner_anchor, winner_idx = unit_winner_info[
                    unit_context.unit_id
                ][scoped_key]
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
                        anchor_segment_id=first_span.anchor_segment_id,
                        selected_text=first_span.selected_text,
                        reason_code=DEDUP_HINT_DUPLICATE_REASON_CODE,
                    )
                )
                skipped_items[-1].update(
                    {
                        "normalized_hint": normalize_dedup_hint(item.dedup_hint),
                        "winner_item_type": winner_t,
                        "winner_anchor_segment_id": winner_anchor,
                        "winner_item_index": winner_idx,
                    }
                )
                continue

            if len(unit_notes[unit_context.unit_id]) >= MAX_GRAMMAR_NOTE_ITEMS:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
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
                            item_index=original_idx,
                            item_type=item_type,
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
                            item_index=original_idx,
                            item_type=item_type,
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
                unit_seen_scoped_keys[unit_context.unit_id].add(scoped_key)
                unit_winner_info[unit_context.unit_id][scoped_key] = (
                    item_type,
                    first_span.anchor_segment_id,
                    original_idx,
                )
            except ValidationError:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
                        anchor_segment_id=first_span.anchor_segment_id,
                        selected_text=first_span.selected_text,
                        reason_code="resolved_item_invalid",
                    )
                )
        else:  # sentence_analysis
            mapping = units_by_segment.get(item.anchor_segment_id)
            if mapping is None:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
                        anchor_segment_id=item.anchor_segment_id,
                        selected_text=item.selected_text,
                        reason_code="anchor_segment_unknown",
                    )
                )
                continue
            unit_context, _ = mapping

            scoped_key = scoped_dedup_key(
                anchor_segment_id=item.anchor_segment_id,
                dedup_hint=item.dedup_hint,
            )
            if scoped_key in unit_seen_scoped_keys[unit_context.unit_id]:
                winner_t, winner_anchor, winner_idx = unit_winner_info[
                    unit_context.unit_id
                ][scoped_key]
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
                        anchor_segment_id=item.anchor_segment_id,
                        selected_text=item.selected_text,
                        reason_code=DEDUP_HINT_DUPLICATE_REASON_CODE,
                    )
                )
                skipped_items[-1].update(
                    {
                        "normalized_hint": normalize_dedup_hint(item.dedup_hint),
                        "winner_item_type": winner_t,
                        "winner_anchor_segment_id": winner_anchor,
                        "winner_item_index": winner_idx,
                    }
                )
                continue

            if len(unit_analyses[unit_context.unit_id]) >= MAX_SENTENCE_ANALYSIS_ITEMS:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
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
                        item_index=original_idx,
                        item_type=item_type,
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
                        item_index=original_idx,
                        item_type=item_type,
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
                unit_seen_scoped_keys[unit_context.unit_id].add(scoped_key)
                unit_winner_info[unit_context.unit_id][scoped_key] = (
                    item_type,
                    item.anchor_segment_id,
                    original_idx,
                )
            except ValidationError:
                skipped_items.append(
                    _build_skip_diagnostic(
                        item_index=original_idx,
                        item_type=item_type,
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
