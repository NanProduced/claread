"""Per-call LangSmith tracing primitives.

These are deliberately small and side-effect-free:

* :func:`disabled_tracing` wraps :func:`langsmith.run_helpers.tracing_context`
  with ``enabled=False`` so a single graph / @traceable call can opt out of
  LangSmith without touching process-global env vars. This is the safe
  replacement for the old ``eval_adapter.shared.trace_scope`` flow that
  mutated ``os.environ["LANGSMITH_PROJECT"]`` per request and was therefore
  unsafe to run concurrently.

* :func:`set_trace_surface` / :func:`get_trace_surface` carry a short
  human-readable "surface" tag through async call chains via
  :class:`contextvars.ContextVar`, so the LangGraph root run config and any
  downstream span can label itself ``analyze_direct``,
  ``eval_workflow_lab``, ``daily_reader_pipeline``, ``overview_worker`` etc.
  without each layer having to plumb the value through every function
  signature.

Neither helper mutates ``os.environ``; both are safe for concurrent
requests in the same process.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)


# Canonical surface taxonomy. New surfaces should be added here so the set
# stays small and greppable; the literal type isn't enforced at runtime,
# but anything outside this list should still be added intentionally.
SURFACE_ANALYZE_DIRECT = "analyze_direct"
SURFACE_EVAL_WORKFLOW_LAB = "eval_workflow_lab"
SURFACE_DAILY_READER_PIPELINE = "daily_reader_pipeline"
SURFACE_OVERVIEW_WORKER = "overview_worker"

KNOWN_SURFACES: frozenset[str] = frozenset(
    {
        SURFACE_ANALYZE_DIRECT,
        SURFACE_EVAL_WORKFLOW_LAB,
        SURFACE_DAILY_READER_PIPELINE,
        SURFACE_OVERVIEW_WORKER,
    }
)


_TRACE_SURFACE: ContextVar[str | None] = ContextVar(
    "claread_trace_surface", default=None
)


@contextmanager
def set_trace_surface(value: str | None) -> Iterator[None]:
    """Bind a ``surface`` label for the current async context.

    A ``None`` value clears the binding back to "unset" so downstream code
    falls through to its own default (e.g. ``analyze_direct`` for the
    public ``/analyze`` route).

    Safe for concurrent requests: ``ContextVar`` copies on task creation.
    """

    token = _TRACE_SURFACE.set(value)
    try:
        yield
    finally:
        _TRACE_SURFACE.reset(token)


def get_trace_surface(default: str) -> str:
    """Return the active surface label, or ``default`` if unset."""

    value = _TRACE_SURFACE.get()
    return value if value else default


@contextmanager
def disabled_tracing() -> Iterator[None]:
    """Disable LangSmith tracing for the wrapped block, per-call only.

    Used by ``eval_adapter.shared.trace_scope`` when an eval request
    explicitly opts ``trace_scope='off'`` (the default). The implementation
    delegates to :func:`langsmith.run_helpers.tracing_context` which sets
    its own ``ContextVar`` and is therefore concurrent-safe — unlike the
    previous implementation that wrote to ``os.environ``.

    If the LangSmith SDK is unavailable or shaped differently in some
    future version, this degrades to a no-op and logs a warning rather
    than failing the request. That fallback is acceptable because the
    upstream caller's contract is "don't pollute traces by default", not
    "guarantee zero traces"; an operator who needs hard isolation should
    set ``LANGSMITH_ENABLED=false`` at process level.
    """

    try:
        from langsmith.run_helpers import tracing_context
    except ImportError:  # pragma: no cover - defensive only
        logger.warning(
            "langsmith.run_helpers.tracing_context unavailable; "
            "disabled_tracing() will be a no-op."
        )
        yield
        return

    with tracing_context(enabled=False):
        yield
