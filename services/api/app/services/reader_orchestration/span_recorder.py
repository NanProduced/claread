"""Reader orchestration span recorder.

Single entry point for writing ``reader_runtime_spans`` rows. The recorder
owns:

- ``trace_id`` propagation via a ``contextvars.ContextVar`` so downstream
  child spans automatically pick up their parent without callers threading
  the value through every signature.
- ``start_span`` / ``end_span`` lifecycle. ``start_span`` INSERTs a
  ``status='started'`` row; ``end_span`` UPDATEs it with the final status,
  duration, failure class/code, token usage, ai_usage_event_id and
  langsmith_run_id linkage.
- A ``reader_span`` async context manager that wraps start/end + try/except
  so worker sites stay clean.

The recorder never raises out of its own calls: span writes are observability
best-effort and must not break the worker's main path. Failures are logged
at warning level and swallowed.

See:
- docs/tmp/reader-orchestration/TMP-reader-orchestration-observability-gap-2026-07-01.md
- infra/migrations/0014_reader_runtime_spans.sql
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Span kind / status constants (mirror the migration CHECK constraints)
# ---------------------------------------------------------------------------

SPAN_KIND_PIPELINE_ROOT = "pipeline_root"
SPAN_KIND_WORKER_TICK = "worker_tick"
SPAN_KIND_LLM_CALL = "llm_call"
SPAN_KIND_PUBLISH_FENCE = "publish_fence"
SPAN_KIND_CLAIM = "claim"
SPAN_KIND_BOOTSTRAP = "bootstrap"

STATUS_STARTED = "started"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_SUPERSEDED = "superseded"
STATUS_SKIPPED = "skipped"

RETRY_CLASS_TRANSIENT = "transient"
RETRY_CLASS_REPAIR = "repair"
RETRY_CLASS_REPLAN = "replan"


# ---------------------------------------------------------------------------
# Span context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpanContext:
    """Returned by ``start_span``; pass to ``end_span`` and child spans."""

    span_id: UUID
    trace_id: UUID
    parent_span_id: UUID | None


_CURRENT_SPAN: ContextVar[SpanContext | None] = ContextVar(
    "claread_reader_span", default=None
)


def current_span() -> SpanContext | None:
    """Return the active span for the current async context, or ``None``."""

    return _CURRENT_SPAN.get()


def derive_retry_class(
    *,
    transient_attempt_count: int,
    repair_attempt_count: int,
    replan_attempt_count: int,
) -> str | None:
    """Map ``reader_jobs.*_attempt_count`` columns to a single retry_class.

    Priority: replan > repair > transient. Returns ``None`` when no retry
    has happened yet (all three counts are 0).
    """

    if replan_attempt_count > 0:
        return RETRY_CLASS_REPLAN
    if repair_attempt_count > 0:
        return RETRY_CLASS_REPAIR
    if transient_attempt_count > 0:
        return RETRY_CLASS_TRANSIENT
    return None


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class ReaderSpanRecorder:
    """Reader orchestration span writer.

    Single entry point for all ``reader_runtime_spans`` INSERTs / UPDATEs.
    Uses a module-level ``ContextVar`` so child spans auto-link to their
    parent without callers threading the value through every signature.

    All writes are best-effort: failures are logged at warning level and
    swallowed so they never break the worker's main path.
    """

    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def start_span(
        self,
        *,
        trace_id: UUID,
        span_kind: str,
        reading_record_id: UUID | None = None,
        parent_span_id: UUID | None = None,
        reader_run_id: UUID | None = None,
        reader_job_id: UUID | None = None,
        worker_type: str | None = None,
        attempt_number: int | None = None,
        retry_class: str | None = None,
        claim_wait_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanContext:
        """INSERT a ``status='started'`` row and return its context.

        Sets the active ``ContextVar`` so child spans auto-link via
        :func:`current_span`. Failures are swallowed and a synthetic
        zero-UUID context is returned so callers can keep going.

        ``reading_record_id`` is optional (NULLable in PG) so publish_fence
        spans can start before the publisher reads ``reader_jobs.reading_record_id``
        inside its transaction. Non-publish spans always pass it.
        """

        span_id = uuid4()
        try:
            async with self.get_pool().acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO reader_runtime_spans (
                        id, trace_id, parent_span_id, span_kind,
                        reader_run_id, reader_job_id, reading_record_id,
                        worker_type, attempt_number, retry_class,
                        status, claim_wait_ms, started_at, metadata_json
                    )
                    VALUES (
                        $1, $2, $3, $4,
                        $5, $6, $7,
                        $8, $9, $10,
                        'started', $11, NOW(), $12::jsonb
                    )
                    """,
                    span_id,
                    trace_id,
                    parent_span_id,
                    span_kind,
                    reader_run_id,
                    reader_job_id,
                    reading_record_id,
                    worker_type,
                    attempt_number,
                    retry_class,
                    claim_wait_ms,
                    jsonb_param(metadata or {}),
                )
        except Exception:
            logger.warning(
                "reader_runtime_spans start_span failed (kind=%s, "
                "record=%s); observability degraded but worker continues.",
                span_kind,
                reading_record_id,
                exc_info=True,
            )

        ctx = SpanContext(
            span_id=span_id,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
        )
        return ctx

    async def end_span(
        self,
        span_ctx: SpanContext,
        *,
        status: str,
        failure_class: str | None = None,
        failure_code: str | None = None,
        model_route: str | None = None,
        model_name: str | None = None,
        model_provider: str | None = None,
        capability_code: str | None = None,
        ai_usage_event_id: UUID | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        langsmith_run_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        """UPDATE the span row with final status / duration / linkage.

        Best-effort: failures are logged and swallowed.

        ``langsmith_run_id`` is auto-populated from the
        :class:`LangSmithIdBridgeProcessor` ContextVar when the caller does
        not pass an explicit value. This couples PG span rows to LangSmith
        runs without requiring every caller to thread the value through.
        """

        if langsmith_run_id is None:
            # Lazy import avoids a hard dependency on the OTel SDK at
            # module load time (tests may run without LangSmith wired).
            try:
                from app.observability.langsmith_span_processor import (
                    get_current_langsmith_ids,
                )
            except ImportError:
                get_current_langsmith_ids = None  # type: ignore[assignment]

            if get_current_langsmith_ids is not None:
                ids = get_current_langsmith_ids()
                if ids is not None:
                    langsmith_run_id = ids.run_id

        try:
            async with self.get_pool().acquire() as conn:
                await conn.execute(
                    """
                    UPDATE reader_runtime_spans
                    SET ended_at = NOW(),
                        duration_ms = EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000,
                        status = $2,
                        failure_class = COALESCE($3, failure_class),
                        failure_code = COALESCE($4, failure_code),
                        model_route = COALESCE($5, model_route),
                        model_name = COALESCE($6, model_name),
                        model_provider = COALESCE($7, model_provider),
                        capability_code = COALESCE($8, capability_code),
                        ai_usage_event_id = COALESCE($9, ai_usage_event_id),
                        input_tokens = COALESCE($10, input_tokens),
                        output_tokens = COALESCE($11, output_tokens),
                        total_tokens = COALESCE($12, total_tokens),
                        cache_read_tokens = COALESCE($13, cache_read_tokens),
                        cache_write_tokens = COALESCE($14, cache_write_tokens),
                        langsmith_run_id = COALESCE($15, langsmith_run_id),
                        metadata_json = CASE
                            WHEN $16::jsonb IS NULL THEN metadata_json
                            ELSE metadata_json || $16::jsonb
                        END
                    WHERE id = $1
                    """,
                    span_ctx.span_id,
                    status,
                    failure_class,
                    failure_code,
                    model_route,
                    model_name,
                    model_provider,
                    capability_code,
                    ai_usage_event_id,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    langsmith_run_id,
                    jsonb_param(extra_metadata) if extra_metadata else None,
                )
        except Exception:
            logger.warning(
                "reader_runtime_spans end_span failed (span_id=%s); "
                "observability degraded but worker continues.",
                span_ctx.span_id,
                exc_info=True,
            )

    @asynccontextmanager
    async def use_span(
        self,
        span_ctx: SpanContext,
    ) -> AsyncIterator[SpanContext]:
        """Bind ``span_ctx`` as the active span for the wrapped block.

        Child spans started inside the block will read it via
        :func:`current_span` and use it as their ``parent_span_id``.

        Restores the previous span on exit so nested spans compose
        correctly.
        """

        token = _CURRENT_SPAN.set(span_ctx)
        try:
            yield span_ctx
        finally:
            _CURRENT_SPAN.reset(token)


# ---------------------------------------------------------------------------
# Module-level singleton + convenience helpers
# ---------------------------------------------------------------------------

_DEFAULT_RECORDER: ReaderSpanRecorder | None = None


def get_default_recorder() -> ReaderSpanRecorder:
    """Return the process-level default recorder.

    Lazily constructed so tests can substitute their own recorder before
    the first call (the same pattern as ``db_connection.DB_POOL``).
    """

    global _DEFAULT_RECORDER
    if _DEFAULT_RECORDER is None:
        _DEFAULT_RECORDER = ReaderSpanRecorder()
    return _DEFAULT_RECORDER


def set_default_recorder(recorder: ReaderSpanRecorder | None) -> None:
    """Override the process-level recorder (mainly for tests)."""

    global _DEFAULT_RECORDER
    _DEFAULT_RECORDER = recorder


def now_utc() -> datetime:
    """Public helper for sites that need a UTC timestamp before start_span."""

    return datetime.now(UTC)
