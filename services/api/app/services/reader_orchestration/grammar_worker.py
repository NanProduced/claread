from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import asyncpg
from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.database import connection as db_connection
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    GrammarNoteItem,
    GrammarNoteLayerOutput,
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

from .job_bootstrap import (
    GRAMMAR_JOB_TYPE,
    GRAMMAR_OPERATION_FINGERPRINT,
    GRAMMAR_TARGET_SCOPE,
)
from .job_runtime import ClaimResult, FenceViolationError, ReaderJobRuntime
from .layer_publisher import GrammarBundleLayerPublisher, PublishedGrammarBundle

DEFAULT_GRAMMAR_RETRY_DELAY = timedelta(minutes=5)
GRAMMAR_WORKFLOW_VERSION = "d5-v4-grammar-worker"
GRAMMAR_MODEL_ROUTE = "reader_layer_grammar_bundle"
FAKE_GRAMMAR_PROMPT_VERSION = "fake-grammar-worker-v1"
FAKE_GRAMMAR_MODEL_PROFILE = "fake-reader-layer-grammar-bundle"
FAKE_GRAMMAR_MODEL_PROVIDER = "fake-provider"
FAKE_GRAMMAR_MODEL_NAME = "fake-grammar-model"
MAX_GRAMMAR_DIAGNOSTIC_ITEMS = 8


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


class GrammarBundleWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        layer_publisher: GrammarBundleLayerPublisher | None = None,
        executor: GrammarBundleExecutor | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._layer_publisher = layer_publisher or GrammarBundleLayerPublisher(pool=pool)
        self._executor = executor or UnconfiguredGrammarBundleExecutor()

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
            or claim.operation_fingerprint != GRAMMAR_OPERATION_FINGERPRINT
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
            await self._record_usage_event(
                context=context,
                execution=execution,
                published_bundle=published_bundle,
                status=STATUS_SUCCEEDED,
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
    ) -> None:
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
        await record_ai_usage_event(
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
    ) -> None:
        if context is None:
            return
        await record_ai_usage_event(
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
