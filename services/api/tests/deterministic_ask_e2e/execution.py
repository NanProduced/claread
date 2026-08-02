"""Deterministic execution swap for the test-only Ask runtime.

Patches ONLY the module-level resolver bindings consumed by the service
layer so the real production stream (``_run_agentic_turn`` →
``run_reading_record_ask`` → coordinator → tools → finalizer →
``build_completed_dto`` → ``message.completed``) runs unchanged against a
deterministic ``FunctionModel``. Send and retry both re-resolve through
``service.resolve_reader_record_ask_execution``, so one patch covers both
while every fence (record scope, envelope identity, generation, retry
execution-version trust) stays production code.

The production auto-wire fallback (``resolve_agentic_model``, used only
when a caller passes no model — unreachable through the HTTP routes) is
replaced with a loud failure so an accidental escape from the explicit
model seam fails closed instead of building a provider model.

Test-only module; never imported from ``app/**``.
"""

from __future__ import annotations

from typing import Any

from .models import build_deterministic_ask_model

__all__ = [
    "build_deterministic_execution_config",
    "install_deterministic_execution",
    "is_installed",
    "uninstall_deterministic_execution",
]

_SERVICE_TARGET = "service.resolve_reader_record_ask_execution"
_STREAM_AUTOWIRE_TARGET = "production_stream.resolve_agentic_model"
_WIRING_AUTOWIRE_TARGET = "production_wiring.resolve_agentic_model"

_originals: dict[str, Any] = {}


def build_deterministic_execution_config(option: Any) -> Any:
    """Build a real ``ReaderRecordAskExecutionConfig`` around the fake model.

    Mirrors the production config shape (provider cap + host usage limit
    + runtime budget) so budget handling in the production stream is
    exercised exactly as with a real model. Web search capability and
    backend are always ``None``: the ``search_web`` tool is never mounted.
    """
    from pydantic_ai.usage import UsageLimits

    from app.services.reader_record_ask.execution_config import (
        ReaderRecordAskExecutionConfig,
    )
    from app.services.reader_record_ask.model_options import (
        ReaderAskRuntimeBudgetConfig,
    )

    return ReaderRecordAskExecutionConfig(
        option_key=str(getattr(option, "key", "deterministic-e2e-r0")),
        model=build_deterministic_ask_model(),
        resolved_model_config=None,
        model_settings_payload={"max_tokens": 3200},
        usage_limits=UsageLimits(output_tokens_limit=9600),
        runtime_budget=ReaderAskRuntimeBudgetConfig(
            max_input_tokens=24000,
            max_output_tokens=3200,
            max_turn_output_tokens=9600,
            prompt_buffer_tokens=800,
        ),
        web_search_capability=None,
        web_search_backend=None,
    )


def _deterministic_resolver(
    option: Any,
    *,
    web_search_mode: str = "disabled",
    settings: Any | None = None,
) -> Any:
    del web_search_mode, settings
    return build_deterministic_execution_config(option)


def _blocked_auto_wire(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError(
        "deterministic Ask e2e runtime: production auto-wire model "
        "resolution must never execute; the HTTP routes always supply an "
        "explicit execution model"
    )


def install_deterministic_execution() -> None:
    """Patch resolver bindings. Idempotent within one process."""
    import app.services.reader_record_ask.production_stream as stream_mod
    import app.services.reader_record_ask.production_wiring as wiring_mod
    import app.services.reader_record_ask.service as service_mod

    _originals.setdefault(_SERVICE_TARGET, service_mod.resolve_reader_record_ask_execution)
    _originals.setdefault(_STREAM_AUTOWIRE_TARGET, stream_mod.resolve_agentic_model)
    _originals.setdefault(_WIRING_AUTOWIRE_TARGET, wiring_mod.resolve_agentic_model)
    service_mod.resolve_reader_record_ask_execution = _deterministic_resolver
    stream_mod.resolve_agentic_model = _blocked_auto_wire
    wiring_mod.resolve_agentic_model = _blocked_auto_wire


def uninstall_deterministic_execution() -> None:
    import app.services.reader_record_ask.production_stream as stream_mod
    import app.services.reader_record_ask.production_wiring as wiring_mod
    import app.services.reader_record_ask.service as service_mod

    if _SERVICE_TARGET in _originals:
        service_mod.resolve_reader_record_ask_execution = _originals.pop(_SERVICE_TARGET)
    if _STREAM_AUTOWIRE_TARGET in _originals:
        stream_mod.resolve_agentic_model = _originals.pop(_STREAM_AUTOWIRE_TARGET)
    if _WIRING_AUTOWIRE_TARGET in _originals:
        wiring_mod.resolve_agentic_model = _originals.pop(_WIRING_AUTOWIRE_TARGET)


def is_installed() -> bool:
    import app.services.reader_record_ask.service as service_mod

    return service_mod.resolve_reader_record_ask_execution is _deterministic_resolver
