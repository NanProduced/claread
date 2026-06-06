"""Observability integrations for the backend service."""

from app.observability.tracing_context import (
    KNOWN_SURFACES,
    SURFACE_ANALYZE_DIRECT,
    SURFACE_DAILY_READER_PIPELINE,
    SURFACE_EVAL_WORKFLOW_LAB,
    SURFACE_OVERVIEW_WORKER,
    disabled_tracing,
    get_trace_surface,
    set_trace_surface,
)

__all__ = [
    "KNOWN_SURFACES",
    "SURFACE_ANALYZE_DIRECT",
    "SURFACE_DAILY_READER_PIPELINE",
    "SURFACE_EVAL_WORKFLOW_LAB",
    "SURFACE_OVERVIEW_WORKER",
    "disabled_tracing",
    "get_trace_surface",
    "set_trace_surface",
]
