from __future__ import annotations

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
from app.schemas.reader_orchestration import TranslationLayerOutput
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

DEFAULT_TRANSLATION_RETRY_DELAY = timedelta(minutes=5)
TRANSLATION_PROMPT_AGENT_NAME = "reader_layer_translation"


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


@dataclass(frozen=True, slots=True)
class TranslationExecutionResult:
    output: TranslationLayerOutput
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
            output_type=TranslationLayerOutput,
            instructions=load_agent_instructions(TRANSLATION_PROMPT_AGENT_NAME),
            name="reader_layer_translation_agent",
            retries={"tools": 1, "output": 2},
        )
        result = await agent.run(_build_translation_prompt(context))
        output = TranslationLayerOutput.model_validate(result.output)
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
            output = TranslationLayerOutput.model_validate(execution.output)
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
    return (
        "Translate the following reading unit.\n"
        f"source_language: {context.source_language}\n"
        f"target_language: {context.target_language}\n"
        f"unit_id: {context.unit_id}\n"
        "Return only the structured TranslationLayerOutput.\n"
        "<source_text>\n"
        f"{context.source_text}\n"
        "</source_text>"
    )


def _build_quality_json(
    output: TranslationLayerOutput,
    execution: TranslationExecutionResult,
) -> dict[str, Any]:
    quality_json: dict[str, Any] = {
        "confidence": output.confidence,
        "notes_count": len(output.notes),
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
