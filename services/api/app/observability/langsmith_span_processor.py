"""LangSmith OTel span → ``reader_runtime_spans.langsmith_run_id`` bridge.

Custom :class:`opentelemetry.sdk.trace.SpanProcessor` that captures
``langsmith.trace.id`` and ``langsmith.span.id`` attributes from
PydanticAI LLM spans (emitted by ``Agent.instrument_all()``) and stores
them in a :class:`contextvars.ContextVar`.

Single-owner contract (RUNTIME-OBSERVABILITY-CLOSURE-R1 / C3): the LangSmith
run id belongs to exactly one span — the ``worker_tick`` that owns the LLM
call. That owner consumes the id explicitly via
:func:`consume_current_langsmith_run_id` (through the
``end_worker_span_success`` / ``end_worker_span_execution_error`` /
``end_worker_span_fence_violation`` / ``end_worker_span_generic_exception``
helpers) and passes it to ``ReaderSpanRecorder.end_span``. The recorder no
longer auto-reads the ContextVar, and the pipeline clears it at every worker
attempt boundary, so a stale id can never leak into a ``publish_fence`` /
``claim`` / ``no_job`` / ``pipeline_root`` span or the next tick.

Best-effort by design: failures are logged at warning level and
swallowed so they never break the worker's main path.

See:
- https://docs.langchain.com/langsmith/trace-with-opentelemetry
- services/api/app/services/reader_orchestration/span_recorder.py
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.trace import Span

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LangSmithIds:
    """Captured LangSmith trace/span IDs from an OTel span."""

    trace_id: str
    span_id: str

    @property
    def run_id(self) -> str:
        """Format suitable for ``reader_runtime_spans.langsmith_run_id``.

        Composite ``"<trace_id>/<span_id>"`` so Console can deep-link to
        ``https://smith.langchain.com/runs/<trace_id>/r/<span_id>`` and so
        a single LangSmith trace with multiple LLM spans stays
        differentiable in PG queries.
        """

        return f"{self.trace_id}/{self.span_id}"


_CURRENT_LANGSMITH_IDS: ContextVar[LangSmithIds | None] = ContextVar(
    "claread_langsmith_ids", default=None
)


def get_current_langsmith_ids() -> LangSmithIds | None:
    """Return the LangSmith IDs captured by the latest LLM span end.

    Returns ``None`` when no LangSmith-managed OTel span has ended in the
    current async context (e.g. tests with ``LANGSMITH_OTEL_ENABLED=false``).
    """

    return _CURRENT_LANGSMITH_IDS.get()


def clear_langsmith_ids() -> None:
    """Reset the ContextVar.

    Used at worker attempt boundaries (clear before an attempt starts and
    reset after it ends) and by tests so a stale LangSmith id never leaks
    into a later-ending span or the next tick.
    """

    _CURRENT_LANGSMITH_IDS.set(None)


def consume_current_langsmith_run_id() -> str | None:
    """Read and clear the current LangSmith ids, returning the run id.

    Single-owner consumption point (C3): only the ``worker_tick`` that owns
    the LLM call invokes this (via the ``end_worker_span_*`` helpers). Reading
    clears the ContextVar so the id cannot be inherited by a span that ends
    later in the same context (``publish_fence`` / the next ``worker_tick``).
    Returns ``None`` when no LangSmith-managed OTel span has ended in the
    current async context.
    """

    ids = _CURRENT_LANGSMITH_IDS.get()
    _CURRENT_LANGSMITH_IDS.set(None)
    return ids.run_id if ids is not None else None


def _extract_langsmith_ids(span: ReadableSpan) -> LangSmithIds | None:
    """Read ``langsmith.trace.id`` / ``langsmith.span.id`` from span attrs.

    Returns ``None`` when either attribute is missing or not a string
    (non-LangSmith spans don't carry these attributes).
    """

    attrs = span.attributes or {}
    trace_id = attrs.get("langsmith.trace.id")
    span_id = attrs.get("langsmith.span.id")
    if not isinstance(trace_id, str) or not isinstance(span_id, str):
        return None
    return LangSmithIds(trace_id=trace_id, span_id=span_id)


class LangSmithIdBridgeProcessor:
    """SpanProcessor that bridges LangSmith OTel IDs to PG span backfill.

    Mounted on the global OTel tracer provider (see
    :func:`app.observability.langsmith._configure_pydantic_ai_otel`). For
    each span end, if the span carries ``langsmith.trace.id`` /
    ``langsmith.span.id`` attributes (LangSmith-managed spans do), the
    values are stored in a ContextVar for the current async context.
    ``ReaderSpanRecorder.end_span`` reads them via
    :func:`get_current_langsmith_ids`.

    Implements the ``opentelemetry.sdk.trace.SpanProcessor`` protocol
    (duck-typed — avoids inheriting from the SDK class so the module
    imports cleanly even when ``opentelemetry-sdk`` is not installed at
    type-check time).
    """

    def __init__(self) -> None:
        self._enabled = True

    def on_start(
        self,
        span: Span,
        parent_context: Context | None = None,
    ) -> None:
        # No-op: we only need on_end to capture IDs.
        return

    def on_end(self, span: ReadableSpan) -> None:
        if not self._enabled:
            return
        try:
            ids = _extract_langsmith_ids(span)
            if ids is not None:
                _CURRENT_LANGSMITH_IDS.set(ids)
        except Exception:
            logger.warning(
                "LangSmithIdBridgeProcessor.on_end failed for span %s; "
                "langsmith_run_id backfill skipped.",
                getattr(span, "name", "<unknown>"),
                exc_info=True,
            )

    def shutdown(self) -> None:
        self._enabled = False

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
