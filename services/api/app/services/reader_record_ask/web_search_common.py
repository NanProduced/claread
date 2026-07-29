"""G3-R1: Canonical Web Search resolver helpers (no circular imports).

Single source of truth for translating a :class:`ResolvedModelConfig`
or a :class:`ResolvedReaderAskModelOption` into either:

1. A :class:`ResolvedWebSearchBinding` (capability + backend from the
   same registry resolution call) — used by the runtime execution path
   (:func:`resolve_reader_record_ask_execution`).
2. A ``"available"`` / ``"unavailable"`` projection — used by the model
   list API (``reader_ask.service``, ``ask_runtime.thread_service``).

All production paths MUST call these helpers — never re-derive
capability or backend separately, and never construct a second
production registry instance outside this module.

Contract
--------
- :func:`resolve_web_search_binding` calls the production registry
  exactly once per invocation and returns the binding.
- :func:`project_web_search_availability` maps a binding to the
  ``"available"`` / ``"unavailable"`` string used by the model list API.
- :func:`resolve_web_search_availability_for_option` is a convenience
  wrapper that resolves the model config from an option + settings and
  projects availability. It is the sole function the model list API
  calls — it must never be duplicated.
- None of these helpers raise on resolution failure — they return a
  disabled binding or ``"unavailable"`` (fail-closed).
"""

from __future__ import annotations

from typing import Literal

from app.config.settings import Settings, get_settings
from app.llm.router import ModelSelectionError, resolve_model_config
from app.llm.routes import MODEL_ROUTE_READER_ASK
from app.llm.types import ResolvedModelConfig
from app.services.reader_ask.model_options import ResolvedReaderAskModelOption
from app.services.reader_record_ask.web_search_adapter_registry import (
    ResolvedWebSearchBinding,
    build_production_web_search_adapter_registry,
)

WebSearchAvailability = Literal["available", "unavailable"]


def resolve_web_search_binding(
    model_config: ResolvedModelConfig,
) -> ResolvedWebSearchBinding:
    """Resolve a binding from the production registry (single call).

    This is the canonical entry point for runtime execution. The
    returned :class:`ResolvedWebSearchBinding` carries BOTH the
    capability AND the backend produced by the same registry resolution
    call — callers must never re-derive one without the other.

    Never raises — adapter construction failures are caught inside the
    registry and returned as a disabled binding.
    """
    registry = build_production_web_search_adapter_registry()
    return registry.resolve(model_config=model_config)


def project_web_search_availability(
    binding: ResolvedWebSearchBinding,
) -> WebSearchAvailability:
    """Project a binding to ``"available"`` or ``"unavailable"``.

    A binding is ``"available"`` only when ALL of:
    - ``capability`` is non-None;
    - ``capability.enabled_for_turn`` is ``True``;
    - ``backend`` is non-None.

    Any other state → ``"unavailable"`` (fail-closed).
    """
    if binding.capability is None:
        return "unavailable"
    if not binding.capability.enabled_for_turn:
        return "unavailable"
    if binding.backend is None:
        return "unavailable"
    return "available"


def resolve_web_search_availability_for_option(
    option: ResolvedReaderAskModelOption,
    *,
    settings: Settings | None = None,
) -> WebSearchAvailability:
    """Resolve web search availability from a model option + settings.

    Convenience wrapper for the model list API. Resolves the
    :class:`ResolvedModelConfig` from ``option.selection`` via the
    router, then calls :func:`resolve_web_search_binding` and
    :func:`project_web_search_availability`.

    Never raises — any resolution failure (unknown profile, missing
    config, adapter construction error) returns ``"unavailable"``.
    """
    cfg = settings or get_settings()
    try:
        model_config = resolve_model_config(
            cfg,
            MODEL_ROUTE_READER_ASK,
            option.selection,
        )
    except ModelSelectionError:
        return "unavailable"
    if model_config is None:
        return "unavailable"
    try:
        binding = resolve_web_search_binding(model_config)
    except Exception:  # noqa: BLE001 — fail-closed, never propagate
        return "unavailable"
    return project_web_search_availability(binding)


__all__ = [
    "WebSearchAvailability",
    "project_web_search_availability",
    "resolve_web_search_availability_for_option",
    "resolve_web_search_binding",
]
