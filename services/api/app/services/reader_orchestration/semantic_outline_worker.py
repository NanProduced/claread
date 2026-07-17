"""Bounded semantic outline worker (T5.3a).

Reads stable base/unit/anchor metadata + bounded previews only.
Model/executor returns candidate refs; publisher maps to opaque ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, Sequence
from uuid import UUID

import asyncpg

from app.database import connection as db_connection

from .job_bootstrap import (
    SEMANTIC_OUTLINE_JOB_TYPE,
    SEMANTIC_OUTLINE_OPERATION_FINGERPRINT,
    SEMANTIC_OUTLINE_TARGET_SCOPE,
)
from .job_runtime import (
    STATUS_FAILED_TERMINAL,
    STATUS_RETRY_LATER,
    STATUS_SUCCEEDED,
    STATUS_SUPERSEDED,
    ClaimResult,
    FenceViolationError,
    ReaderJobRuntime,
)
from .semantic_outline import SemanticOutlineAnchor, SemanticOutlineUnit
from .semantic_outline_publisher import (
    SemanticOutlineCandidateNode,
    SemanticOutlineLayerPublisher,
    SemanticOutlinePublishResult,
)

DEFAULT_SEMANTIC_OUTLINE_RETRY_DELAY = timedelta(minutes=5)
SEMANTIC_OUTLINE_WORKER_VERSION = "reader-semantic-outline-worker-v1"

# Bounded input constants (parameterizable; not product length freezes).
OUTLINE_MAX_UNIT_PREVIEW_CHARS = 160
OUTLINE_MAX_TOTAL_PREVIEW_CHARS = 8000
OUTLINE_MAX_ATTEMPTED_NODES = 64
OUTLINE_MAX_DEPTH = 3
OUTLINE_TITLE_MAX = 80
OUTLINE_MAX_UNITS_FOR_PREVIEW = 200


@dataclass(frozen=True, slots=True)
class SemanticOutlineUnitPreview:
    unit_id: str
    order_index: int
    unit_type: str
    preview: str


@dataclass(frozen=True, slots=True)
class SemanticOutlineWorkerInput:
    base_id: str
    generation: int
    units: tuple[SemanticOutlineUnitPreview, ...]
    anchors: tuple[tuple[str, str], ...]  # (anchor_segment_id, unit_id)
    total_preview_chars: int


@dataclass(frozen=True, slots=True)
class SemanticOutlineExecutionResult:
    candidates: tuple[SemanticOutlineCandidateNode, ...]
    worker_failure: bool = False
    model: str | None = None
    usage_data: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SemanticOutlineJobContext:
    job_id: UUID
    run_id: UUID
    reading_record_id: UUID
    user_id: UUID
    base_id: UUID
    expected_generation: int
    operation_fingerprint: str
    attempt_count: int
    max_attempts: int
    worker_input: SemanticOutlineWorkerInput


@dataclass(frozen=True, slots=True)
class SemanticOutlineJobProcessResult:
    claim: ClaimResult
    context: SemanticOutlineJobContext | None
    status: str
    publish_result: SemanticOutlinePublishResult | None = None
    error_code: str | None = None


class SemanticOutlineGenerator(Protocol):
    async def generate(
        self, context: SemanticOutlineJobContext
    ) -> SemanticOutlineExecutionResult: ...


class SemanticOutlineGenerationError(Exception):
    """Generator-side failure with permanent vs transient classification.

    Permanent (``retryable=False``): configuration / 4xx-class parameter
    errors — worker must fail-closed without blind retry.
    Transient (``retryable=True``): reuses the job's bounded retry path.
    """

    def __init__(
        self,
        message: str,
        *,
        failure_class: str,
        failure_code: str,
        retryable: bool = False,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.retryable = retryable
        self.model = model


class UnconfiguredSemanticOutlineGenerator:
    """Production-safe default: no LLM call, permanent configuration failure.

    Real LLM outline requires an explicit model route + prompt agent + profile
    (not yet registered). Do not invent those here; inject
    :class:`FakeSemanticOutlineGenerator` in tests or a future real adapter
    only after route/prompt exist.
    """

    async def generate(
        self, context: SemanticOutlineJobContext
    ) -> SemanticOutlineExecutionResult:
        del context
        raise SemanticOutlineGenerationError(
            (
                "semantic outline generator is not configured; inject an "
                "explicit FakeSemanticOutlineGenerator (tests) or a real "
                "adapter once MODEL_ROUTE + prompt + profile exist"
            ),
            failure_class="configuration",
            failure_code="semantic_outline_generator_unconfigured",
            retryable=False,
        )


class FakeSemanticOutlineGenerator:
    """Test / controlled double: returns configured candidates or worker_failure.

    Never used as the production default; inject via DI.
    """

    def __init__(
        self,
        candidates: Sequence[SemanticOutlineCandidateNode] | None = None,
        *,
        worker_failure: bool = False,
        model: str = "fake-outline-model",
    ) -> None:
        self.candidates = tuple(candidates or ())
        self.worker_failure = worker_failure
        self.model = model
        self.calls: list[SemanticOutlineJobContext] = []

    async def generate(
        self, context: SemanticOutlineJobContext
    ) -> SemanticOutlineExecutionResult:
        self.calls.append(context)
        return SemanticOutlineExecutionResult(
            candidates=self.candidates,
            worker_failure=self.worker_failure,
            model=self.model,
            usage_data={
                "aggregate": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                }
            },
        )


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError):
        if hasattr(row, "get"):
            return row.get(key, default)
        return default


def build_bounded_worker_input(
    *,
    base_id: str,
    generation: int,
    unit_rows: Sequence[Any],
    anchor_rows: Sequence[Any] = (),
    max_unit_preview_chars: int = OUTLINE_MAX_UNIT_PREVIEW_CHARS,
    max_total_preview_chars: int = OUTLINE_MAX_TOTAL_PREVIEW_CHARS,
    max_units_for_preview: int = OUTLINE_MAX_UNITS_FOR_PREVIEW,
) -> SemanticOutlineWorkerInput:
    """Build bounded unit previews; always keeps unit identity metadata."""
    units: list[SemanticOutlineUnitPreview] = []
    total = 0
    for index, row in enumerate(unit_rows):
        unit_id = str(row["unit_id"])
        order_index = int(row["order_index"])
        unit_type = str(_row_get(row, "unit_type") or "body")
        raw_text = str(_row_get(row, "text") or _row_get(row, "unit_text") or "")
        if index < max_units_for_preview and total < max_total_preview_chars:
            remaining = max_total_preview_chars - total
            cap = min(max_unit_preview_chars, remaining)
            preview = raw_text[:cap] if cap > 0 else ""
            total += len(preview)
        else:
            preview = ""
        units.append(
            SemanticOutlineUnitPreview(
                unit_id=unit_id,
                order_index=order_index,
                unit_type=unit_type,
                preview=preview,
            )
        )
    anchors: list[tuple[str, str]] = []
    for row in anchor_rows:
        anchor_id = _row_get(row, "anchor_segment_id")
        unit_id = _row_get(row, "unit_id")
        if anchor_id is not None and unit_id is not None:
            anchors.append((str(anchor_id), str(unit_id)))
    return SemanticOutlineWorkerInput(
        base_id=base_id,
        generation=generation,
        units=tuple(units),
        anchors=tuple(anchors),
        total_preview_chars=total,
    )


def clamp_candidates(
    candidates: Sequence[SemanticOutlineCandidateNode],
    *,
    max_nodes: int = OUTLINE_MAX_ATTEMPTED_NODES,
) -> tuple[SemanticOutlineCandidateNode, ...]:
    return tuple(candidates[:max_nodes])


class SemanticOutlineWorkerService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        generator: SemanticOutlineGenerator | None = None,
        publisher: SemanticOutlineLayerPublisher | None = None,
        job_runtime: ReaderJobRuntime | None = None,
    ) -> None:
        self._pool = pool
        # Default is fail-closed Unconfigured — never auto-calls a real LLM.
        self._generator = generator or UnconfiguredSemanticOutlineGenerator()
        self._publisher = publisher or SemanticOutlineLayerPublisher(pool=pool)
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def claim_semantic_outline_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> ClaimResult | None:
        claim = await self._job_runtime.claim_next_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
            job_type=SEMANTIC_OUTLINE_JOB_TYPE,
            target_type=SEMANTIC_OUTLINE_TARGET_SCOPE,
            operation_fingerprint=SEMANTIC_OUTLINE_OPERATION_FINGERPRINT,
        )
        if claim is None:
            return None
        await self._mark_run_running(claim.run_id)
        return claim

    async def claim_semantic_outline_job_for_record(
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
            job_type=SEMANTIC_OUTLINE_JOB_TYPE,
            target_type=SEMANTIC_OUTLINE_TARGET_SCOPE,
            operation_fingerprint=SEMANTIC_OUTLINE_OPERATION_FINGERPRINT,
            reading_record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
        )
        if claim is None:
            return None
        await self._mark_run_running(claim.run_id)
        return claim

    async def process_next_semantic_outline_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_SEMANTIC_OUTLINE_RETRY_DELAY,
    ) -> SemanticOutlineJobProcessResult | None:
        claim = await self.claim_semantic_outline_job(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_semantic_outline_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_next_semantic_outline_job_for_record(
        self,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        lease_owner: str,
        lease_duration: timedelta,
        retry_delay: timedelta = DEFAULT_SEMANTIC_OUTLINE_RETRY_DELAY,
    ) -> SemanticOutlineJobProcessResult | None:
        claim = await self.claim_semantic_outline_job_for_record(
            record_id=record_id,
            base_id=base_id,
            expected_generation=expected_generation,
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if claim is None:
            return None
        return await self.process_claimed_semantic_outline_job(
            claim=claim,
            retry_delay=retry_delay,
        )

    async def process_claimed_semantic_outline_job(
        self,
        *,
        claim: ClaimResult,
        retry_delay: timedelta = DEFAULT_SEMANTIC_OUTLINE_RETRY_DELAY,
    ) -> SemanticOutlineJobProcessResult:
        context: SemanticOutlineJobContext | None = None
        try:
            context = await self._load_job_context(claim.job_id)
            execution = await self._generator.generate(context)
            candidates = clamp_candidates(execution.candidates)
            units = tuple(
                SemanticOutlineUnit(unit_id=u.unit_id, order_index=u.order_index)
                for u in context.worker_input.units
            )
            anchors = tuple(
                SemanticOutlineAnchor(anchor_segment_id=a[0], unit_id=a[1])
                for a in context.worker_input.anchors
            )
            try:
                publish_result = await self._publisher.publish_from_candidates(
                    job_id=claim.job_id,
                    lease_token=claim.lease_token,
                    reading_record_id=context.reading_record_id,
                    base_id=context.base_id,
                    generation=context.expected_generation,
                    operation_fingerprint=context.operation_fingerprint,
                    source_run_id=context.run_id,
                    source_job_id=context.job_id,
                    units=units,
                    anchors=anchors,
                    candidates=candidates,
                    worker_failure=execution.worker_failure,
                    model=execution.model,
                )
            except FenceViolationError:
                await self._mark_claimed_job_superseded(
                    claim, rationale_code="publish_fence_failed"
                )
                raise

            if publish_result.outcome in {"published", "idempotent_reuse"}:
                await self._complete_job_success(
                    claim=claim,
                    context=context,
                    publish_result=publish_result,
                )
                return SemanticOutlineJobProcessResult(
                    claim=claim,
                    context=context,
                    status="succeeded",
                    publish_result=publish_result,
                )

            # V=0 / failed / worker_failure: fail-closed, preserve old layer.
            if context.attempt_count >= context.max_attempts or execution.worker_failure:
                await self._complete_job_failed_terminal(
                    claim=claim,
                    context=context,
                    publish_result=publish_result,
                    failure_code="semantic_outline_validation_failed"
                    if not execution.worker_failure
                    else "semantic_outline_worker_failure",
                )
                return SemanticOutlineJobProcessResult(
                    claim=claim,
                    context=context,
                    status="failed_terminal",
                    publish_result=publish_result,
                    error_code="semantic_outline_not_published",
                )

            await self._complete_job_retry_later(
                claim=claim,
                context=context,
                publish_result=publish_result,
                available_at=datetime.now(UTC) + retry_delay,
            )
            return SemanticOutlineJobProcessResult(
                claim=claim,
                context=context,
                status="retry_later",
                publish_result=publish_result,
                error_code="semantic_outline_retryable",
            )
        except FenceViolationError:
            raise
        except SemanticOutlineGenerationError as exc:
            # Permanent configuration / 4xx-class: no blind retry; job + run
            # must terminalize together (no run stuck in running/queued).
            # Transient: bounded retry_later until max_attempts; run stays
            # running so the next claim can resume.
            terminal = (not exc.retryable) or (
                context is not None and context.attempt_count >= context.max_attempts
            )
            failure_message = str(exc)[:240]
            if terminal:
                await self._complete_generation_error_failed_terminal(
                    claim=claim,
                    failure_class=exc.failure_class,
                    failure_code=exc.failure_code,
                    failure_message=failure_message,
                )
                return SemanticOutlineJobProcessResult(
                    claim=claim,
                    context=context,
                    status="failed_terminal",
                    error_code=exc.failure_code,
                )
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status=STATUS_RETRY_LATER,
                lease_token=claim.lease_token,
                available_at=datetime.now(UTC) + retry_delay,
                failure_class=exc.failure_class,
                failure_code=exc.failure_code,
                failure_message=failure_message,
                rationale_code=exc.failure_code,
            )
            # Keep run open (running) for the next attempt — do not finish it.
            return SemanticOutlineJobProcessResult(
                claim=claim,
                context=context,
                status="retry_later",
                error_code=exc.failure_code,
            )
        except Exception as exc:
            if context is not None and context.attempt_count >= context.max_attempts:
                await self._job_runtime.transition(
                    job_id=claim.job_id,
                    target_status=STATUS_FAILED_TERMINAL,
                    lease_token=claim.lease_token,
                    failure_class="worker",
                    failure_code=type(exc).__name__,
                    failure_message=str(exc)[:240],
                    rationale_code="semantic_outline_worker_error",
                )
                return SemanticOutlineJobProcessResult(
                    claim=claim,
                    context=context,
                    status="failed_terminal",
                    error_code=type(exc).__name__,
                )
            await self._job_runtime.transition(
                job_id=claim.job_id,
                target_status=STATUS_RETRY_LATER,
                lease_token=claim.lease_token,
                available_at=datetime.now(UTC) + retry_delay,
                failure_class="worker",
                failure_code=type(exc).__name__,
                failure_message=str(exc)[:240],
                rationale_code="semantic_outline_worker_error",
            )
            return SemanticOutlineJobProcessResult(
                claim=claim,
                context=context,
                status="retry_later",
                error_code=type(exc).__name__,
            )

    async def _mark_run_running(self, run_id: UUID) -> None:
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_runs
                SET status = 'running',
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE id = $1
                """,
                run_id,
            )

    async def _load_job_context(self, job_id: UUID) -> SemanticOutlineJobContext:
        async with self.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT job.id,
                       job.run_id,
                       job.reading_record_id,
                       job.user_id,
                       job.base_id,
                       job.expected_generation,
                       job.operation_fingerprint,
                       job.attempt_count,
                       job.max_attempts,
                       job.target_key
                FROM reader_jobs job
                WHERE job.id = $1
                """,
                job_id,
            )
            if row is None:
                raise LookupError(f"reader job {job_id} not found")

            base_row = await conn.fetchrow(
                """
                SELECT text
                FROM reading_bases
                WHERE id = $1
                  AND reading_record_id = $2
                """,
                row["base_id"],
                row["reading_record_id"],
            )
            base_text = str(base_row["text"]) if base_row is not None else ""
            unit_rows_raw = await conn.fetch(
                """
                SELECT unit_id, order_index, unit_type,
                       base_start_utf16, base_end_utf16
                FROM reading_units
                WHERE reading_record_id = $1
                  AND base_id = $2
                ORDER BY order_index ASC
                """,
                row["reading_record_id"],
                row["base_id"],
            )
            unit_rows: list[dict[str, Any]] = []
            for unit in unit_rows_raw:
                start = int(unit["base_start_utf16"])
                end = int(unit["base_end_utf16"])
                # UTF-16 aware slice is ideal; for bounded preview, code-point
                # approximation from Python str indices is acceptable fail-soft
                # when offsets align with BMP text (production bases store
                # UTF-16 offsets; display_title uses dedicated slicer).
                try:
                    from app.contracts.annotation import slice_by_utf16_offsets

                    unit_text = slice_by_utf16_offsets(base_text, start, end)
                except Exception:
                    unit_text = base_text[start:end]
                unit_rows.append(
                    {
                        "unit_id": unit["unit_id"],
                        "order_index": unit["order_index"],
                        "unit_type": unit["unit_type"],
                        "unit_text": unit_text,
                    }
                )
            anchor_rows = await conn.fetch(
                """
                SELECT anchor_segment_id, unit_id
                FROM anchor_segments
                WHERE reading_record_id = $1
                  AND base_id = $2
                ORDER BY order_index ASC
                LIMIT 500
                """,
                row["reading_record_id"],
                row["base_id"],
            )
            worker_input = build_bounded_worker_input(
                base_id=str(row["base_id"]),
                generation=int(row["expected_generation"]),
                unit_rows=unit_rows,
                anchor_rows=anchor_rows,
            )
            return SemanticOutlineJobContext(
                job_id=row["id"],
                run_id=row["run_id"],
                reading_record_id=row["reading_record_id"],
                user_id=row["user_id"],
                base_id=row["base_id"],
                expected_generation=int(row["expected_generation"]),
                operation_fingerprint=str(row["operation_fingerprint"]),
                attempt_count=int(row["attempt_count"]),
                max_attempts=int(row["max_attempts"]),
                worker_input=worker_input,
            )

    async def _complete_job_success(
        self,
        *,
        claim: ClaimResult,
        context: SemanticOutlineJobContext,
        publish_result: SemanticOutlinePublishResult,
    ) -> None:
        output_ref = {
            "layer_id": str(publish_result.layer_id) if publish_result.layer_id else None,
            "outline_revision": publish_result.outline_revision,
            "status": publish_result.status,
            "outcome": publish_result.outcome,
            "reused_existing": publish_result.reused_existing,
            "worker_version": SEMANTIC_OUTLINE_WORKER_VERSION,
        }
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status=STATUS_SUCCEEDED,
            lease_token=claim.lease_token,
            output_ref=output_ref,
        )
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_runs
                SET status = 'completed',
                    finished_at = NOW(),
                    updated_at = NOW()
                WHERE id = $1
                """,
                claim.run_id,
            )

    async def _complete_job_failed_terminal(
        self,
        *,
        claim: ClaimResult,
        context: SemanticOutlineJobContext,
        publish_result: SemanticOutlinePublishResult | None,
        failure_code: str,
    ) -> None:
        output_ref = {
            "outcome": publish_result.outcome if publish_result else "not_published",
            "status": publish_result.status if publish_result else "failed",
            "outline_revision": publish_result.outline_revision if publish_result else None,
            "worker_version": SEMANTIC_OUTLINE_WORKER_VERSION,
        }
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status=STATUS_FAILED_TERMINAL,
            lease_token=claim.lease_token,
            output_ref=output_ref,
            failure_class="validation",
            failure_code=failure_code,
            failure_message=failure_code,
            rationale_code=failure_code,
        )
        await self._mark_run_failed_terminal(
            claim.run_id,
            failure_class="validation",
            failure_code=failure_code,
        )

    async def _complete_generation_error_failed_terminal(
        self,
        *,
        claim: ClaimResult,
        failure_class: str,
        failure_code: str,
        failure_message: str,
    ) -> None:
        """Terminalize job and its reader_run for permanent generation errors."""
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status=STATUS_FAILED_TERMINAL,
            lease_token=claim.lease_token,
            failure_class=failure_class,
            failure_code=failure_code,
            failure_message=failure_message,
            rationale_code=failure_code,
        )
        await self._mark_run_failed_terminal(
            claim.run_id,
            failure_class=failure_class,
            failure_code=failure_code,
        )

    async def _mark_run_failed_terminal(
        self,
        run_id: UUID,
        *,
        failure_class: str,
        failure_code: str,
    ) -> None:
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_runs
                SET status = 'failed_terminal',
                    failure_class = $2,
                    failure_code = $3,
                    finished_at = COALESCE(finished_at, NOW()),
                    updated_at = NOW()
                WHERE id = $1
                  AND status IN (
                      'queued', 'running', 'waiting_user', 'waiting_quota', 'paused'
                  )
                """,
                run_id,
                failure_class,
                failure_code,
            )

    async def _complete_job_retry_later(
        self,
        *,
        claim: ClaimResult,
        context: SemanticOutlineJobContext,
        publish_result: SemanticOutlinePublishResult | None,
        available_at: datetime,
    ) -> None:
        output_ref = {
            "outcome": publish_result.outcome if publish_result else "not_published",
            "status": publish_result.status if publish_result else "failed",
            "worker_version": SEMANTIC_OUTLINE_WORKER_VERSION,
        }
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status=STATUS_RETRY_LATER,
            lease_token=claim.lease_token,
            available_at=available_at,
            output_ref=output_ref,
            failure_class="validation",
            failure_code="semantic_outline_retryable",
            failure_message="semantic outline not publishable; retry",
            rationale_code="semantic_outline_retryable",
        )

    async def _mark_claimed_job_superseded(
        self,
        claim: ClaimResult,
        *,
        rationale_code: str,
    ) -> None:
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status=STATUS_SUPERSEDED,
            lease_token=claim.lease_token,
            rationale_code=rationale_code,
        )
