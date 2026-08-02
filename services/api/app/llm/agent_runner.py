from __future__ import annotations

from typing import Any

from pydantic_ai.usage import RunUsage

from app.config.settings import get_settings
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.router import build_model_for_route
from app.llm.routes import ModelRoute
from app.llm.types import ModelSelection, ResolvedModelConfig
from app.observability.workflow_tracing import build_usage_metadata


async def run_reader_scoped_agent(
    agent: Any,
    prompt: str,
    **run_kwargs: Any,
) -> Any:
    """Single entry for Reader layer ``agent.run`` with execution correlation.

    Reader workers (translation / vocabulary / grammar / window / display
    title) must call this instead of ``agent.run`` so that, when a Reader
    ``ExecutionCorrelation`` is bound:

    - a new ``agent_run_id`` is minted **before** the provider call
    - the id is stored on the active correlation ContextVar (survives
      exceptions)
    - on success it is also attached as ``result._claread_agent_run_id``
    - **agent-run duration** is measured with ``time.perf_counter`` around
      ``agent.run`` only (local monotonic; **not** provider latency)
    - **provider-request duration** is recorded only if the result/usage
      exposes a known timing field; otherwise status is ``unavailable``

    When no execution scope is bound (non-Reader callers), behaviour is a
    plain ``agent.run`` with no Reader identity. Never a provider HTTP id.
    Never writes ``ai_usage_events.latency_ms``.
    """
    # Lazy import avoids circular import with reader_orchestration package init.
    import time

    from app.services.ai_usage.execution_diagnostics import (
        build_duration_provenance,
        current_execution,
        mint_agent_run_id,
        set_last_duration_provenance,
    )

    agent_run_id = None
    reader_scope = current_execution() is not None
    if reader_scope:
        agent_run_id, _updated = mint_agent_run_id()

    started = time.perf_counter()
    try:
        result = await agent.run(prompt, **run_kwargs)
    except BaseException:
        # agent_run_id already bound; still record local agent-run duration
        # and explicit provider-timing unavailable for failure spans/events.
        if reader_scope:
            elapsed_ms = int(round((time.perf_counter() - started) * 1000))
            set_last_duration_provenance(
                build_duration_provenance(
                    agent_run_duration_ms=elapsed_ms,
                    agent_run_id=agent_run_id,
                    result=None,
                    usage_data=None,
                )
            )
        raise

    if reader_scope:
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        # Provider timing only from dedicated adapter envelope on result —
        # never from usage.details / generic timing maps.
        provenance = build_duration_provenance(
            agent_run_duration_ms=elapsed_ms,
            agent_run_id=agent_run_id,
            result=result,
        )
        set_last_duration_provenance(provenance)
        try:
            result._claread_agent_run_id = agent_run_id
            result._claread_agent_run_duration_ms = elapsed_ms
            result._claread_duration_provenance = provenance
        except Exception:  # pragma: no cover - immutable result edge
            pass
    return result


async def run_agent_with_route(
    *,
    agent: Any,
    prompt: str,
    deps: Any,
    route: ModelRoute,
    model_selection: ModelSelection | None = None,
) -> Any:
    model, model_config = build_model_for_route(get_settings(), route, model_selection)
    if model is None:
        raise RuntimeError(f"model route is not configured: {route}")
    assert_real_llm_allowed(
        "app.llm.agent_runner.run_agent_with_route",
        model_config=model_config,
    )
    # Shared path: Reader-scope mint happens inside run_reader_scoped_agent.
    result = await run_reader_scoped_agent(
        agent,
        prompt,
        deps=deps,
        model=model,
    )
    result._resolved_model_config = model_config
    return result


def extract_model_metadata(model_config: ResolvedModelConfig | None) -> dict[str, str]:
    if model_config is None:
        return {
            "model_name": "unknown",
            "profile_name": "unknown",
            "ls_provider": "unknown",
            "ls_model_name": "unknown",
        }
    return {
        "model_name": model_config.model_name,
        "profile_name": model_config.profile_name,
        "ls_provider": model_config.provider,
        "ls_model_name": model_config.model_name,
    }


def extract_run_usage(result: Any) -> dict[str, object] | None:
    """Extract provider usage metadata; emit Reader usage-presence diagnostics.

    Diagnostics fire only when ``current_execution()`` is bound (Reader worker
    scope). Non-Reader callers keep pre-O2 extract-only behaviour.

    Never logs prompt, article text, raw provider payloads, or secrets.
    """
    # Lazy import avoids circular import with reader_orchestration package init.
    from app.services.ai_usage.execution_diagnostics import (
        STAGE_ADAPTER,
        classify_usage_presence,
        current_execution,
        log_usage_diagnostic,
    )

    correlation = current_execution()
    model_config = getattr(result, "_resolved_model_config", None)
    provider = getattr(model_config, "provider", None) if model_config else None
    model = getattr(model_config, "model_name", None) if model_config else None

    def _emit_adapter_diagnostic(
        usage_data: dict[str, object] | None,
        *,
        usage_is_none: bool,
    ) -> None:
        # Isolate non-Reader: no diagnostic without active execution scope.
        if correlation is None:
            return
        presence = classify_usage_presence(
            usage_data,
            stage=STAGE_ADAPTER,
            provider=provider,
            model=model,
            capability_code=correlation.capability_code,
        )
        log_usage_diagnostic(
            diagnostic_code=presence.diagnostic_code,
            stage=STAGE_ADAPTER,
            correlation=correlation,
            usage_key_list=presence.usage_key_list,
            normalized_totals=presence.normalized_totals,
            provider=provider,
            model=model,
            extra={
                "usage_is_none": usage_is_none,
                "usage_is_empty_mapping": presence.usage_is_empty_mapping,
            },
        )

    usage = getattr(result, "usage", None)
    if usage is None:
        _emit_adapter_diagnostic(None, usage_is_none=True)
        return None
    if not isinstance(usage, RunUsage) and callable(usage):
        usage = usage()
    if usage is None:
        _emit_adapter_diagnostic(None, usage_is_none=True)
        return None
    metadata = build_usage_metadata(usage)
    _emit_adapter_diagnostic(metadata, usage_is_none=False)
    return metadata
