from __future__ import annotations

import json
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
    VOCABULARY_JOB_TYPE,
    VOCABULARY_OPERATION_FINGERPRINT,
    VOCABULARY_TARGET_SCOPE,
)
from .job_runtime import ClaimResult, FenceViolationError, ReaderJobRuntime
from .layer_publisher import PublishedVocabularyLayer, VocabularyLayerPublisher

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


@dataclass(frozen=True, slots=True)
class VocabularyAnchorSegmentContext:
    anchor_segment_id: str
    sentence_id: str
    segment_type: str
    unit_start_utf16: int
    unit_end_utf16: int
    text_hash: str
    text: str


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
            retries=1,
            output_retries=2,
            instrument=False,
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


class VocabularyWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        layer_publisher: VocabularyLayerPublisher | None = None,
        executor: VocabularyExecutor | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._layer_publisher = layer_publisher or VocabularyLayerPublisher(pool=pool)
        self._executor = executor or PydanticAIVocabularyExecutor()

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
            or claim.operation_fingerprint != VOCABULARY_OPERATION_FINGERPRINT
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
            or claim.operation_fingerprint != VOCABULARY_OPERATION_FINGERPRINT
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
            await self._record_usage_event(
                context=context,
                execution=execution,
                published_layer=published_layer,
                status=STATUS_SUCCEEDED,
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
            return VocabularyJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
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
                )
            )

        if not anchor_segments:
            raise VocabularyExecutionError(
                f"vocabulary unit {row['target_key']} has no anchor segments",
                retryable=False,
                failure_class="validation",
                failure_code="missing_anchor_segments",
            )

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
    ) -> None:
        await record_ai_usage_event(
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
    ) -> None:
        if context is None:
            return
        await record_ai_usage_event(
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
        "Return only the structured candidate output.\n"
        "<source_text>\n"
        f"{context.source_text}\n"
        "</source_text>\n"
        "<anchor_segments_json>\n"
        f"{json.dumps(anchor_segments, ensure_ascii=False)}\n"
        "</anchor_segments_json>"
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
