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
    TRANSLATION_JOB_TYPE,
    TRANSLATION_OPERATION_FINGERPRINT,
    TRANSLATION_TARGET_SCOPE,
)
from .job_runtime import ClaimResult, FenceViolationError, ReaderJobRuntime
from .layer_publisher import PublishedTranslationLayer, TranslationLayerPublisher
from .reading_strategy import (
    ReaderStrategyResolverError,
    resolve_reader_variant_strategy,
)

DEFAULT_TRANSLATION_RETRY_DELAY = timedelta(minutes=5)
TRANSLATION_PROMPT_AGENT_NAME = "reader_layer_translation"

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


class TranslationWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        layer_publisher: TranslationLayerPublisher | None = None,
        translator: TranslationExecutor | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._layer_publisher = layer_publisher or TranslationLayerPublisher(pool=pool)
        self._translator = translator or PydanticAITranslationExecutor()

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
            await self._record_usage_event(
                context=context,
                execution=execution,
                published_layer=published_layer,
                status=STATUS_SUCCEEDED,
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
            return TranslationJobProcessResult(
                claim=claim,
                context=context,
                status="failed_terminal",
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
    ) -> None:
        await record_ai_usage_event(
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
    ) -> None:
        if context is None:
            return
        await record_ai_usage_event(
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
