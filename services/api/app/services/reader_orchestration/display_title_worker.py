from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent

from app.config.settings import Settings, get_settings
from app.contracts.annotation import slice_by_utf16_offsets
from app.database import connection as db_connection
from app.database.json_compat import ensure_json_object, jsonb_param
from app.llm.agent_runner import extract_run_usage
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.router import build_model_for_route
from app.llm.routes import MODEL_ROUTE_READER_TITLE_GENERATION
from app.services.ai_usage import (
    BILLING_MODE_INTERNAL_ONLY,
    CAPABILITY_READER_TITLE_GENERATION,
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

from ._text import sanitize_failure_message
from .job_bootstrap import (
    DISPLAY_TITLE_JOB_TYPE,
    DISPLAY_TITLE_OPERATION_FINGERPRINT,
    DISPLAY_TITLE_TARGET_SCOPE,
)
from .job_runtime import (
    STATUS_CLAIMED,
    STATUS_RETRY_LATER,
    ClaimResult,
    FenceViolationError,
    ReaderJobRuntime,
)
from .job_runtime import (
    STATUS_SUCCEEDED as JOB_STATUS_SUCCEEDED,
)

DEFAULT_DISPLAY_TITLE_RETRY_DELAY = timedelta(minutes=5)
DEFAULT_DISPLAY_TITLE_FAILURE_MESSAGE = "display title generation failed"
DISPLAY_TITLE_PROMPT_AGENT_NAME = "reader_title_generation"
DISPLAY_TITLE_WORKER_VERSION = "reader-display-title-worker-v1"
MAX_GENERATED_TITLE_CHARS = 32
MAX_TITLE_SOURCE_CHARS = 3600
MAX_TITLE_HEADING_CHARS = 900
MAX_TITLE_BLOCK_SCAN_ROWS = 40
MAX_TITLE_BLOCKS = 8

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_MARKETING_RE = re.compile(r"(震惊|必看|一文读懂|揭秘|重磅|爆款|逆天|刷屏|真相)")


class DisplayTitleStructuredOutput(BaseModel):
    title_zh: str = Field(min_length=1, max_length=MAX_GENERATED_TITLE_CHARS)


@dataclass(frozen=True, slots=True)
class DisplayTitleGenerationInput:
    source_title: str | None
    source_type: str
    source_language: str
    input_strategy: str
    section_headings: tuple[str, ...]
    content_preview: str
    preview_char_length: int
    base_char_length: int
    source_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DisplayTitleJobContext:
    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    base_id: UUID
    expected_generation: int
    operation_fingerprint: str
    attempt_count: int
    title_input: DisplayTitleGenerationInput


@dataclass(frozen=True, slots=True)
class DisplayTitleExecutionResult:
    title_zh: str
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str = MODEL_ROUTE_READER_TITLE_GENERATION
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


@dataclass(frozen=True, slots=True)
class DisplayTitleJobProcessResult:
    claim: ClaimResult
    context: DisplayTitleJobContext | None
    status: str
    title_zh: str | None = None
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_route: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


class DisplayTitleGenerationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_class: str,
        failure_code: str,
        rationale_code: str = "display_title_generation_failed",
        prompt_version: str | None = None,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.rationale_code = rationale_code
        self.prompt_version = prompt_version
        self.model_profile = model_profile
        self.model_provider = model_provider
        self.model_name = model_name


class DisplayTitleGenerator(Protocol):
    async def generate(
        self,
        context: DisplayTitleJobContext,
    ) -> DisplayTitleExecutionResult:
        ...


class PydanticAIDisplayTitleGenerator:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _build_agent(self, *, model: Any) -> Agent:
        return Agent(
            model=model,
            output_type=DisplayTitleStructuredOutput,
            instructions=load_agent_instructions(DISPLAY_TITLE_PROMPT_AGENT_NAME),
            name="reader_title_generation_agent",
            retries={"tools": 1, "output": 2},
        )

    async def _run_agent(self, agent: Agent, prompt: str) -> Any:
        return await agent.run(prompt)

    async def generate(
        self,
        context: DisplayTitleJobContext,
    ) -> DisplayTitleExecutionResult:
        settings = self._settings or get_settings()
        prompt_version = get_prompt_version()
        if not str(settings.reader_title_model_profile or "").strip():
            raise DisplayTitleGenerationError(
                (
                    "display title generator is not configured; set "
                    "reader_title_model_profile or inject an explicit fake generator"
                ),
                failure_class="configuration",
                failure_code="display_title_generator_unconfigured",
                prompt_version=prompt_version,
            )

        model, model_config = build_model_for_route(
            settings,
            MODEL_ROUTE_READER_TITLE_GENERATION,
        )
        if model is None:
            raise DisplayTitleGenerationError(
                "reader_title_generation model route is not configured",
                failure_class="configuration",
                failure_code="model_route_unavailable",
                prompt_version=prompt_version,
            )

        assert_real_llm_allowed(
            (
                "app.services.reader_orchestration.display_title_worker."
                "PydanticAIDisplayTitleGenerator"
            ),
            model_config=model_config,
        )

        agent = self._build_agent(model=model)
        try:
            result = await self._run_agent(agent, _build_display_title_prompt(context))
        except DisplayTitleGenerationError:
            raise
        except Exception as exc:
            raise DisplayTitleGenerationError(
                f"reader_title_generation agent execution failed: {exc}",
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
            structured = DisplayTitleStructuredOutput.model_validate(result.output)
            title_zh = normalize_generated_title_zh(structured.title_zh)
        except (ValidationError, ValueError) as exc:
            raise DisplayTitleGenerationError(
                f"reader_title_generation produced invalid title output: {exc}",
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

        return DisplayTitleExecutionResult(
            title_zh=title_zh,
            usage_data=extract_run_usage(result),
            prompt_version=prompt_version,
            model_profile=(
                str(model_config.profile_name) if model_config is not None else None
            ),
            model_provider=(
                str(model_config.provider) if model_config is not None else None
            ),
            model_name=str(model_config.model_name) if model_config is not None else None,
        )


class DisplayTitleWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        generator: DisplayTitleGenerator | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = ReaderJobRuntime(pool=pool)
        self._generator = generator or PydanticAIDisplayTitleGenerator()

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def claim_display_title_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> ClaimResult | None:
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=DISPLAY_TITLE_JOB_TYPE,
            target_type=DISPLAY_TITLE_TARGET_SCOPE,
            operation_fingerprint=DISPLAY_TITLE_OPERATION_FINGERPRINT,
        )
        if claim is None:
            return None
        await self._mark_run_running(claim.run_id)
        await self._mark_record_title_pending(claim)
        return claim

    async def claim_display_title_job_for_record(
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
            job_type=DISPLAY_TITLE_JOB_TYPE,
            target_type=DISPLAY_TITLE_TARGET_SCOPE,
            operation_fingerprint=DISPLAY_TITLE_OPERATION_FINGERPRINT,
            reading_record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if claim is None:
            return None
        await self._mark_run_running(claim.run_id)
        await self._mark_record_title_pending(claim)
        return claim

    async def process_next_display_title_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_DISPLAY_TITLE_RETRY_DELAY,
    ) -> DisplayTitleJobProcessResult | None:
        claim = await self.claim_display_title_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_display_title_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_next_display_title_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_DISPLAY_TITLE_RETRY_DELAY,
    ) -> DisplayTitleJobProcessResult | None:
        claim = await self.claim_display_title_job_for_record(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_display_title_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_claimed_display_title_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_DISPLAY_TITLE_RETRY_DELAY,
    ) -> DisplayTitleJobProcessResult:
        context: DisplayTitleJobContext | None = None
        try:
            context = await self._load_job_context(claim.job_id)
            execution = await self._generator.generate(context)
            title_zh = normalize_generated_title_zh(execution.title_zh)
            await self._complete_title_job_success(
                claim=claim,
                context=context,
                execution=execution,
                title_zh=title_zh,
            )
            await self._record_usage_event(
                context=context,
                execution=execution,
                status=STATUS_SUCCEEDED,
            )
            return DisplayTitleJobProcessResult(
                claim=claim,
                context=context,
                status="succeeded",
                title_zh=title_zh,
                usage_data=execution.usage_data,
                prompt_version=execution.prompt_version,
                model_route=execution.model_route,
                model_profile=execution.model_profile,
                model_provider=execution.model_provider,
                model_name=execution.model_name,
            )
        except FenceViolationError:
            await self._mark_claimed_job_superseded(claim, rationale_code="publish_fence_failed")
            raise
        except DisplayTitleGenerationError as exc:
            try:
                await self._complete_title_job_failed_retryable(
                    claim=claim,
                    context=context,
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    failure_message=str(exc),
                    rationale_code=exc.rationale_code,
                    available_at=datetime.now(UTC) + retry_delay,
                )
            except FenceViolationError:
                await self._mark_claimed_job_superseded(
                    claim,
                    rationale_code="publish_fence_failed",
                )
                raise
            await self._record_failed_usage_event(
                context=context,
                error_code=exc.failure_code,
                error_message=str(exc),
                model_profile=exc.model_profile,
                model_provider=exc.model_provider,
                model_name=exc.model_name,
                prompt_version=exc.prompt_version,
            )
            return DisplayTitleJobProcessResult(
                claim=claim,
                context=context,
                status="retry_later",
            )
        except Exception as exc:
            failure_code = type(exc).__name__
            failure_message = f"reader_title_generation failed unexpectedly: {exc}"
            try:
                await self._complete_title_job_failed_retryable(
                    claim=claim,
                    context=context,
                    failure_class="worker",
                    failure_code=failure_code,
                    failure_message=failure_message,
                    rationale_code="display_title_generation_failed",
                    available_at=datetime.now(UTC) + retry_delay,
                )
            except FenceViolationError:
                await self._mark_claimed_job_superseded(
                    claim,
                    rationale_code="publish_fence_failed",
                )
                raise
            await self._record_failed_usage_event(
                context=context,
                error_code=failure_code,
                error_message=failure_message,
            )
            return DisplayTitleJobProcessResult(
                claim=claim,
                context=context,
                status="retry_later",
            )

    async def _load_job_context(self, job_id: UUID) -> DisplayTitleJobContext:
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
                       job.attempt_count,
                       record.title AS record_title,
                       record.source_type,
                       base.language AS source_language,
                       base.text AS base_text,
                       base.content_utf16_length,
                       base.title_snapshot,
                       COALESCE(input.metadata_json, '{}'::jsonb) AS source_metadata
                FROM reader_jobs job
                JOIN reading_records record
                  ON record.id = job.reading_record_id
                 AND record.user_id = job.user_id
                JOIN reading_bases base
                  ON base.id = job.base_id
                 AND base.reading_record_id = job.reading_record_id
                LEFT JOIN LATERAL (
                    SELECT metadata_json
                    FROM original_inputs
                    WHERE reading_record_id = job.reading_record_id
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                ) input ON TRUE
                WHERE job.id = $1
                """,
                job_id,
            )
            if row is None:
                raise LookupError(f"reader job {job_id} not found")
            if str(row["target_key"]) != str(row["reading_record_id"]):
                raise DisplayTitleGenerationError(
                    "display title job target_key must be the reading_record_id",
                    failure_class="validation",
                    failure_code="invalid_title_job_target",
                )

            stable_rows = await conn.fetch(
                """
                SELECT doc.title AS stable_document_title,
                       block.block_type,
                       block.text_content,
                       block.order_index
                FROM stable_reading_documents doc
                LEFT JOIN stable_document_blocks block
                  ON block.stable_document_id = doc.id
                WHERE doc.reading_record_id = $1
                  AND doc.record_generation = $2
                  AND doc.status = 'active'
                ORDER BY block.order_index ASC NULLS LAST
                LIMIT $3
                """,
                row["reading_record_id"],
                int(row["expected_generation"]),
                MAX_TITLE_BLOCK_SCAN_ROWS,
            )
            unit_rows = await conn.fetch(
                """
                SELECT unit_id, unit_type, order_index, base_start_utf16, base_end_utf16
                FROM reading_units
                WHERE reading_record_id = $1
                  AND base_id = $2
                ORDER BY order_index ASC
                LIMIT $3
                """,
                row["reading_record_id"],
                row["base_id"],
                MAX_TITLE_BLOCK_SCAN_ROWS,
            )

        base_text = str(row["base_text"])
        title_input = build_display_title_generation_input(
            record_title=_clean_text(row["record_title"]),
            base_title_snapshot=_clean_text(row["title_snapshot"]),
            source_type=str(row["source_type"]),
            source_language=str(row["source_language"] or "en"),
            source_metadata=ensure_json_object(row["source_metadata"]),
            base_text=base_text,
            base_char_length=int(row["content_utf16_length"]),
            stable_rows=stable_rows,
            unit_rows=unit_rows,
        )
        return DisplayTitleJobContext(
            job_id=row["id"],
            run_id=row["run_id"],
            reading_record_id=row["reading_record_id"],
            user_id=row["user_id"],
            base_id=row["base_id"],
            expected_generation=int(row["expected_generation"]),
            operation_fingerprint=str(row["operation_fingerprint"]),
            attempt_count=int(row["attempt_count"]),
            title_input=title_input,
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

    async def _mark_record_title_pending(self, claim: ClaimResult) -> None:
        if claim.base_id is None:
            return
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reading_records
                SET title_generation_status = 'pending',
                    title_generation_error_code = NULL,
                    title_generation_error_message = NULL,
                    title_generation_updated_at = NOW(),
                    updated_at = NOW()
                WHERE id = $1
                  AND user_id = $2
                  AND active_base_id = $3
                  AND generation = $4
                  AND title_generation_status <> 'succeeded'
                  AND deleted_at IS NULL
                """,
                claim.reading_record_id,
                await self._load_job_user_id(claim.job_id),
                claim.base_id,
                claim.expected_generation,
            )

    async def _load_job_user_id(self, job_id: UUID) -> UUID:
        async with self.get_pool().acquire() as conn:
            user_id = await conn.fetchval("SELECT user_id FROM reader_jobs WHERE id = $1", job_id)
        if not isinstance(user_id, UUID):
            raise LookupError(f"reader job {job_id} not found")
        return user_id

    async def _complete_title_job_success(
        self,
        *,
        claim: ClaimResult,
        context: DisplayTitleJobContext,
        execution: DisplayTitleExecutionResult,
        title_zh: str,
    ) -> None:
        now = datetime.now(UTC)
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await _load_claimed_job_for_completion(conn, claim)
                fence_error = await _validate_job_fence(conn, job_row)
                if fence_error is not None:
                    raise FenceViolationError(f"display title publish fence failed: {fence_error}")

                updated_record = await conn.fetchrow(
                    """
                    UPDATE reading_records
                    SET generated_title_zh = $5,
                        title_generation_status = 'succeeded',
                        title_generation_error_code = NULL,
                        title_generation_error_message = NULL,
                        title_generation_attempt_count = $6,
                        title_generation_updated_at = $7,
                        updated_at = $7
                    WHERE id = $1
                      AND user_id = $2
                      AND active_base_id = $3
                      AND generation = $4
                      AND deleted_at IS NULL
                    RETURNING id
                    """,
                    context.reading_record_id,
                    context.user_id,
                    context.base_id,
                    context.expected_generation,
                    title_zh,
                    context.attempt_count,
                    now,
                )
                if updated_record is None:
                    raise FenceViolationError("display title record update fence failed")

                await conn.execute(
                    """
                    UPDATE reader_jobs
                    SET status = 'succeeded',
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_expires_at = NULL,
                        claimed_at = NULL,
                        output_ref_json = $2::jsonb,
                        failure_class = NULL,
                        failure_code = NULL,
                        failure_message = NULL,
                        rationale_code = NULL,
                        updated_at = $3
                    WHERE id = $1
                    """,
                    claim.job_id,
                    jsonb_param(
                        {
                            "generated_title_zh": title_zh,
                            "input_strategy": context.title_input.input_strategy,
                            "prompt_version": execution.prompt_version,
                            "model_route": execution.model_route,
                            "model_profile": execution.model_profile,
                        }
                    ),
                    now,
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    claim.run_id,
                    now,
                )
                await _insert_reader_job_event(
                    conn,
                    claim=claim,
                    event_type="job_succeeded",
                    payload={"target_status": JOB_STATUS_SUCCEEDED},
                )
                await _insert_title_reader_event(
                    conn,
                    claim=claim,
                    event_type="record_state_changed",
                    payload={
                        "record_id": str(context.reading_record_id),
                        "base_id": str(context.base_id),
                        "generation": context.expected_generation,
                        "field": "display_title_zh",
                        "title_generation_status": "succeeded",
                    },
                    created_at=now,
                )

    async def _complete_title_job_failed_retryable(
        self,
        *,
        claim: ClaimResult,
        context: DisplayTitleJobContext | None,
        failure_class: str,
        failure_code: str,
        failure_message: str,
        rationale_code: str,
        available_at: datetime,
    ) -> None:
        now = datetime.now(UTC)
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await _load_claimed_job_for_completion(conn, claim)
                fence_error = await _validate_job_fence(conn, job_row)
                if fence_error is not None:
                    raise FenceViolationError(f"display title failure fence failed: {fence_error}")

                record_id = context.reading_record_id if context else claim.reading_record_id
                user_id = context.user_id if context else job_row["user_id"]
                base_id = context.base_id if context else job_row["base_id"]
                generation = (
                    context.expected_generation
                    if context
                    else int(job_row["expected_generation"])
                )
                attempt_count = context.attempt_count if context else int(job_row["attempt_count"])
                sanitized_message = sanitize_failure_message(
                    failure_message,
                    default=DEFAULT_DISPLAY_TITLE_FAILURE_MESSAGE,
                ) or DEFAULT_DISPLAY_TITLE_FAILURE_MESSAGE

                updated_record = await conn.fetchrow(
                    """
                    UPDATE reading_records
                    SET generated_title_zh = NULL,
                        title_generation_status = 'failed_retryable',
                        title_generation_error_code = $5,
                        title_generation_error_message = $6,
                        title_generation_attempt_count = $7,
                        title_generation_updated_at = $8,
                        updated_at = $8
                    WHERE id = $1
                      AND user_id = $2
                      AND active_base_id = $3
                      AND generation = $4
                      AND deleted_at IS NULL
                      AND title_generation_status <> 'succeeded'
                    RETURNING id
                    """,
                    record_id,
                    user_id,
                    base_id,
                    generation,
                    failure_code,
                    sanitized_message,
                    attempt_count,
                    now,
                )
                if updated_record is None:
                    raise FenceViolationError("display title failed state update fence failed")

                await conn.execute(
                    """
                    UPDATE reader_jobs
                    SET status = 'retry_later',
                        available_at = $2,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_expires_at = NULL,
                        claimed_at = NULL,
                        failure_class = $3,
                        failure_code = $4,
                        failure_message = $5,
                        rationale_code = $6,
                        updated_at = $7
                    WHERE id = $1
                    """,
                    claim.job_id,
                    available_at,
                    failure_class,
                    failure_code,
                    sanitized_message,
                    rationale_code,
                    now,
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'failed_retryable',
                        failure_class = $2,
                        failure_code = $3,
                        finished_at = NULL,
                        updated_at = $4
                    WHERE id = $1
                    """,
                    claim.run_id,
                    failure_class,
                    failure_code,
                    now,
                )
                await _insert_reader_job_event(
                    conn,
                    claim=claim,
                    event_type="job_retry_scheduled",
                    payload={
                        "target_status": STATUS_RETRY_LATER,
                        "available_at": available_at.isoformat(),
                        "failure_code": failure_code,
                        "rationale_code": rationale_code,
                    },
                )
                await _insert_title_reader_event(
                    conn,
                    claim=claim,
                    event_type="record_state_changed",
                    payload={
                        "record_id": str(record_id),
                        "base_id": str(base_id),
                        "generation": generation,
                        "field": "display_title_zh",
                        "title_generation_status": "failed_retryable",
                        "failure_code": failure_code,
                    },
                    created_at=now,
                )

    async def _mark_claimed_job_superseded(
        self,
        claim: ClaimResult,
        *,
        rationale_code: str,
    ) -> None:
        now = datetime.now(UTC)
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                await _load_claimed_job_for_completion(conn, claim)
                await conn.execute(
                    """
                    UPDATE reader_jobs
                    SET status = 'superseded',
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_expires_at = NULL,
                        claimed_at = NULL,
                        rationale_code = $2,
                        updated_at = $3
                    WHERE id = $1
                    """,
                    claim.job_id,
                    rationale_code,
                    now,
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'superseded',
                        failure_class = 'publish_guard',
                        failure_code = $2,
                        finished_at = $3,
                        updated_at = $3
                    WHERE id = $1
                    """,
                    claim.run_id,
                    rationale_code,
                    now,
                )
                await _insert_reader_job_event(
                    conn,
                    claim=claim,
                    event_type="job_superseded",
                    payload={"rationale_code": rationale_code},
                )

    async def _record_usage_event(
        self,
        *,
        context: DisplayTitleJobContext,
        execution: DisplayTitleExecutionResult,
        status: str,
    ) -> None:
        await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_TITLE_GENERATION,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=status,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version=DISPLAY_TITLE_WORKER_VERSION,
                prompt_version=execution.prompt_version,
                model_route=execution.model_route,
                model_profile_id=execution.model_profile,
                model_profile=execution.model_profile,
                model_provider=execution.model_provider,
                model_name=execution.model_name,
                planner_kind="llm_worker",
                usage_data=execution.usage_data,
                operation_fingerprint=context.operation_fingerprint,
                metadata_json=_usage_metadata(context),
            )
        )

    async def _record_failed_usage_event(
        self,
        *,
        context: DisplayTitleJobContext | None,
        error_code: str,
        error_message: str,
        prompt_version: str | None = None,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> None:
        if context is None:
            return
        await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
                capability_code=CAPABILITY_READER_TITLE_GENERATION,
                billing_mode=BILLING_MODE_INTERNAL_ONLY,
                status=STATUS_FAILED,
                user_id=context.user_id,
                reading_record_id=context.reading_record_id,
                reader_run_id=context.run_id,
                reader_job_id=context.job_id,
                workflow_name="reader_orchestration",
                workflow_version=DISPLAY_TITLE_WORKER_VERSION,
                prompt_version=prompt_version,
                model_route=MODEL_ROUTE_READER_TITLE_GENERATION,
                model_profile_id=model_profile,
                model_profile=model_profile,
                model_provider=model_provider,
                model_name=model_name,
                planner_kind="llm_worker",
                operation_fingerprint=context.operation_fingerprint,
                error_code=error_code,
                error_message=(
                    sanitize_failure_message(
                        error_message,
                        default=DEFAULT_DISPLAY_TITLE_FAILURE_MESSAGE,
                    )
                    or DEFAULT_DISPLAY_TITLE_FAILURE_MESSAGE
                ),
                metadata_json=_usage_metadata(context),
            )
        )


def build_display_title_generation_input(
    *,
    record_title: str | None,
    base_title_snapshot: str | None,
    source_type: str,
    source_language: str,
    source_metadata: dict[str, Any],
    base_text: str,
    base_char_length: int,
    stable_rows: list[asyncpg.Record] | tuple[asyncpg.Record, ...],
    unit_rows: list[asyncpg.Record] | tuple[asyncpg.Record, ...],
) -> DisplayTitleGenerationInput:
    stable_input = _input_from_stable_blocks(
        record_title=record_title,
        base_title_snapshot=base_title_snapshot,
        source_type=source_type,
        source_language=source_language,
        source_metadata=source_metadata,
        base_char_length=base_char_length,
        stable_rows=stable_rows,
    )
    if stable_input is not None:
        return stable_input

    headings: list[str] = []
    blocks: list[str] = []
    for row in unit_rows:
        unit_text = slice_by_utf16_offsets(
            base_text,
            int(row["base_start_utf16"]),
            int(row["base_end_utf16"]),
        )
        cleaned = _clean_text(unit_text)
        if not cleaned:
            continue
        if str(row["unit_type"]) == "heading":
            headings.append(cleaned[:120])
            continue
        blocks.append(cleaned)
        if len(blocks) >= MAX_TITLE_BLOCKS:
            break

    if blocks:
        preview = _bounded_join(blocks)
    else:
        cleaned_base = _clean_text(base_text)
        preview = cleaned_base[:MAX_TITLE_SOURCE_CHARS] if cleaned_base else ""
    return DisplayTitleGenerationInput(
        source_title=base_title_snapshot or record_title,
        source_type=source_type,
        source_language=source_language,
        input_strategy="reading_units_preview" if blocks or headings else "base_text_preview",
        section_headings=tuple(_bounded_headings(headings)),
        content_preview=preview,
        preview_char_length=len(preview),
        base_char_length=base_char_length,
        source_metadata=source_metadata,
    )


def normalize_generated_title_zh(value: str) -> str:
    title = _clean_text(value)
    title = title.strip("#*_`\"'“”‘’《》〈〉[]()（） ")
    title = re.sub(r"[。！？!?.:：；;，,]+$", "", title).strip()
    if not title:
        raise ValueError("generated title is blank")
    if not _CJK_RE.search(title):
        raise ValueError("generated title must contain Chinese characters")
    if len(title) > MAX_GENERATED_TITLE_CHARS:
        raise ValueError("generated title is too long for reader masthead")
    if _MARKETING_RE.search(title):
        raise ValueError("generated title uses marketing-style wording")
    return title


def _input_from_stable_blocks(
    *,
    record_title: str | None,
    base_title_snapshot: str | None,
    source_type: str,
    source_language: str,
    source_metadata: dict[str, Any],
    base_char_length: int,
    stable_rows: list[asyncpg.Record] | tuple[asyncpg.Record, ...],
) -> DisplayTitleGenerationInput | None:
    if not stable_rows:
        return None

    stable_title = _clean_text(stable_rows[0]["stable_document_title"])
    headings: list[str] = []
    blocks: list[str] = []
    for row in stable_rows:
        block_type = row["block_type"]
        text = _clean_text(row["text_content"])
        if block_type is None or not text:
            continue
        if str(block_type) == "heading":
            headings.append(text[:120])
            continue
        if str(block_type) in {"paragraph", "blockquote", "list_item", "image_ocr", "caption"}:
            blocks.append(text)
        if len(blocks) >= MAX_TITLE_BLOCKS:
            break

    if not blocks and not headings and not stable_title:
        return None

    preview = _bounded_join(blocks)
    return DisplayTitleGenerationInput(
        source_title=stable_title or base_title_snapshot or record_title,
        source_type=source_type,
        source_language=source_language,
        input_strategy="stable_document_blocks",
        section_headings=tuple(_bounded_headings(headings)),
        content_preview=preview,
        preview_char_length=len(preview),
        base_char_length=base_char_length,
        source_metadata=source_metadata,
    )


def _build_display_title_prompt(context: DisplayTitleJobContext) -> str:
    title_input = context.title_input
    headings = "\n".join(f"- {heading}" for heading in title_input.section_headings)
    if not headings:
        headings = "(none)"
    return (
        "Generate one Simplified Chinese display title for a Claread reader header.\n"
        "Return only the structured DisplayTitleStructuredOutput.\n"
        f"record_id: {context.reading_record_id}\n"
        f"source_type: {title_input.source_type}\n"
        f"source_language: {title_input.source_language}\n"
        f"input_strategy: {title_input.input_strategy}\n"
        f"base_char_length: {title_input.base_char_length}\n"
        f"preview_char_length: {title_input.preview_char_length}\n"
        f"source_title: {title_input.source_title or '(none)'}\n"
        "<section_headings>\n"
        f"{headings}\n"
        "</section_headings>\n"
        "<content_preview>\n"
        f"{title_input.content_preview}\n"
        "</content_preview>"
    )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def _bounded_join(blocks: list[str]) -> str:
    selected: list[str] = []
    used = 0
    for block in blocks:
        cleaned = _clean_text(block)
        if not cleaned:
            continue
        remaining = MAX_TITLE_SOURCE_CHARS - used
        if remaining <= 0:
            break
        excerpt = cleaned[: min(len(cleaned), remaining)]
        selected.append(excerpt)
        used += len(excerpt) + 2
        if len(selected) >= MAX_TITLE_BLOCKS:
            break
    return "\n\n".join(selected)


def _bounded_headings(headings: list[str]) -> list[str]:
    selected: list[str] = []
    used = 0
    for heading in headings:
        cleaned = _clean_text(heading)
        if not cleaned:
            continue
        remaining = MAX_TITLE_HEADING_CHARS - used
        if remaining <= 0:
            break
        excerpt = cleaned[: min(len(cleaned), remaining)]
        selected.append(excerpt)
        used += len(excerpt) + 1
    return selected


async def _load_claimed_job_for_completion(
    conn: asyncpg.Connection,
    claim: ClaimResult,
) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE", claim.job_id)
    if row is None:
        raise LookupError(f"reader job {claim.job_id} not found")
    if row["status"] != STATUS_CLAIMED:
        raise ValueError(f"display title job {claim.job_id} is not claimed")
    if row["run_id"] != claim.run_id:
        raise ValueError(f"display title job {claim.job_id} run_id mismatch")
    if UUID(str(row["lease_token"])) != claim.lease_token:
        raise ValueError(f"display title job {claim.job_id} lease token mismatch")
    lease_expires_at = row["lease_expires_at"]
    if lease_expires_at is None or lease_expires_at <= datetime.now(UTC):
        raise ValueError(f"display title job {claim.job_id} lease expired")
    return row


async def _validate_job_fence(
    conn: asyncpg.Connection,
    job_row: asyncpg.Record,
) -> str | None:
    base_id = job_row["base_id"]
    if base_id is None:
        return "missing_base"
    row = await conn.fetchrow(
        """
        SELECT r.generation,
               r.active_base_id,
               r.deleted_at,
               b.status AS base_status,
               b.record_generation
        FROM reading_records r
        LEFT JOIN reading_bases b
          ON b.id = $2
         AND b.reading_record_id = r.id
        WHERE r.id = $1
          AND r.user_id = $3
        """,
        job_row["reading_record_id"],
        base_id,
        job_row["user_id"],
    )
    if row is None or row["deleted_at"] is not None:
        return "record_missing"
    expected_generation = int(job_row["expected_generation"])
    if int(row["generation"]) != expected_generation:
        return "stale_generation"
    if row["active_base_id"] != base_id:
        return "active_base_mismatch"
    if row["base_status"] != "active":
        return "inactive_base"
    if int(row["record_generation"]) != expected_generation:
        return "base_generation_mismatch"
    return None


async def _insert_reader_job_event(
    conn: asyncpg.Connection,
    *,
    claim: ClaimResult,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    await conn.execute(
        """
        INSERT INTO reader_job_events
            (reading_record_id, run_id, job_id, event_type, payload_json)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        claim.reading_record_id,
        claim.run_id,
        claim.job_id,
        event_type,
        jsonb_param(payload),
    )


async def _insert_title_reader_event(
    conn: asyncpg.Connection,
    *,
    claim: ClaimResult,
    event_type: str,
    payload: dict[str, Any],
    created_at: datetime,
) -> None:
    sequence = await conn.fetchval(
        """
        UPDATE reader_event_sequences
        SET next_sequence = next_sequence + 1,
            updated_at = $2
        WHERE reading_record_id = $1
        RETURNING next_sequence - 1
        """,
        claim.reading_record_id,
        created_at,
    )
    if not isinstance(sequence, int):
        raise ValueError(f"reader_event_sequences missing for record {claim.reading_record_id}")
    await conn.execute(
        """
        INSERT INTO reader_events (
            id,
            reading_record_id,
            sequence,
            event_type,
            payload_json,
            source_run_id,
            source_job_id,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
        """,
        uuid4(),
        claim.reading_record_id,
        sequence,
        event_type,
        jsonb_param(payload),
        claim.run_id,
        claim.job_id,
        created_at,
    )

def _usage_metadata(context: DisplayTitleJobContext) -> dict[str, Any]:
    title_input = context.title_input
    return {
        "base_id": str(context.base_id),
        "input_strategy": title_input.input_strategy,
        "source_type": title_input.source_type,
        "source_language": title_input.source_language,
        "preview_char_length": title_input.preview_char_length,
        "base_char_length": title_input.base_char_length,
    }
