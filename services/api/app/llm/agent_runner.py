from __future__ import annotations

import time
from typing import Any

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse, RetryPromptPart
from pydantic_ai.usage import RunUsage
from pydantic_graph import End

from app.config.settings import get_settings
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.router import build_model_for_route
from app.llm.routes import ModelRoute
from app.llm.types import ModelSelection, ResolvedModelConfig
from app.observability.workflow_tracing import build_usage_metadata
from app.services.ai_usage.execution_diagnostics import (
    MAX_PROVIDER_RESPONSES_IN_SNAPSHOT,
    USAGE_COMPLETENESS_COMPLETE,
    USAGE_COMPLETENESS_PARTIAL,
    USAGE_COMPLETENESS_UNAVAILABLE,
    AgentRunUsageSnapshot,
    ProviderResponseObservation,
    build_duration_provenance,
    current_execution,
    mint_agent_run_id,
    set_last_agent_run_usage_snapshot,
    set_last_duration_provenance,
)


async def run_reader_scoped_agent(
    agent: Any,
    prompt: str,
    **run_kwargs: Any,
) -> Any:
    """Single entry for Reader layer agent execution with execution correlation.

    Reader workers (translation / vocabulary / grammar / window / display
    title) must call this instead of ``agent.run`` so that, when a Reader
    ``ExecutionCorrelation`` is bound:

    - a new ``agent_run_id`` is minted **before** the provider call
    - the id is stored on the active correlation ContextVar (survives
      exceptions)
    - on success it is also attached as ``result._claread_agent_run_id``
    - **agent-run duration** is measured with ``time.perf_counter`` around
      the agent run only (local monotonic; **not** provider latency)
    - **provider-request duration** is recorded only if the result/usage
      exposes a known timing field; otherwise status is ``unavailable``
    - on BOTH success and failure an immutable ``AgentRunUsageSnapshot``
      (built from public ``AgentRun`` APIs only) is stored on a ContextVar
      so workers can persist confirmed provider usage even when the run
      raises

    Reader scope drives the run via ``agent.iter`` + ``AgentRun.next`` so
    capability hooks fire exactly as with ``agent.run``. Failure paths
    re-raise the ORIGINAL exception untouched (no wrapping, no attribute
    mutation; ``__cause__`` / traceback preserved).

    When no execution scope is bound (non-Reader callers), the run still
    uses ``agent.iter`` so confirmed provider usage can be recovered after
    a structured-output failure. Never a provider HTTP id. Never writes
    ``ai_usage_events.latency_ms``.
    """
    correlation = current_execution()
    if correlation is None:
        agent_run: Any = None
        try:
            async with agent.iter(prompt, **run_kwargs) as agent_run:
                node = agent_run.next_node
                while not isinstance(node, End):
                    if agent_run.result is not None:
                        break
                    node = await agent_run.next(node)
        except BaseException as exc:
            _attach_failed_run_usage(exc, agent_run)
            raise
        return agent_run.result

    agent_run_id, _updated = mint_agent_run_id()
    # Never inherit a stale snapshot from an earlier run in this scope.
    set_last_agent_run_usage_snapshot(None)

    started = time.perf_counter()
    agent_run: Any = None
    try:
        # The with-body must NOT catch node exceptions: agent.iter's
        # __aexit__ gives capability wrap_run / on_run_error their chance
        # to recover (same semantics as agent.run). A recovered run exits
        # the context manager normally with agent_run.result set; an
        # unrecovered one re-raises the ORIGINAL error from here.
        async with agent.iter(prompt, **run_kwargs) as agent_run:
            node = agent_run.next_node
            while not isinstance(node, End):
                # wrap_run short-circuit: result already available.
                if agent_run.result is not None:
                    break
                node = await agent_run.next(node)
    except BaseException as exc:
        # Unrecovered failure: snapshot from public AgentRun APIs, record
        # duration, then bare-raise the original exception untouched
        # (cause chain / traceback preserved, no wrapping, no mutation).
        if agent_run is not None:
            snapshot = _build_usage_snapshot(
                agent_run,
                execution_id=correlation.execution_id,
                agent_run_id=agent_run_id,
                error=exc,
            )
            set_last_agent_run_usage_snapshot(snapshot)
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

    # Success OR capability-recovered run: agent_run.result is available.
    result = agent_run.result
    snapshot = _build_usage_snapshot(
        agent_run,
        execution_id=correlation.execution_id,
        agent_run_id=agent_run_id,
        error=None,
    )
    set_last_agent_run_usage_snapshot(snapshot)
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


def _build_usage_snapshot(
    agent_run: Any,
    *,
    execution_id: Any,
    agent_run_id: Any,
    error: BaseException | None,
) -> AgentRunUsageSnapshot:
    """Build the snapshot from public ``AgentRun`` APIs; never raises."""
    try:
        usage: RunUsage | None = getattr(agent_run, "usage", None)
        usage_data = build_usage_metadata(usage) if usage is not None else None

        responses: list[ProviderResponseObservation] = []
        retry_prompt_count = 0
        messages = agent_run.new_messages()
        for message in messages:
            if isinstance(message, ModelResponse):
                ordinal = len(responses) + 1
                responses.append(
                    ProviderResponseObservation(
                        ordinal=ordinal,
                        provider_response_id=message.provider_response_id,
                        input_tokens=int(message.usage.input_tokens or 0),
                        output_tokens=int(message.usage.output_tokens or 0),
                        cache_read_tokens=int(message.usage.cache_read_tokens or 0),
                        cache_write_tokens=int(message.usage.cache_write_tokens or 0),
                        finish_reason=message.finish_reason,
                    )
                )
            else:
                for part in getattr(message, "parts", ()):
                    if isinstance(part, RetryPromptPart):
                        retry_prompt_count += 1

        response_count = len(responses)
        truncated = max(0, response_count - MAX_PROVIDER_RESPONSES_IN_SNAPSHOT)
        completeness = _classify_completeness(error, response_count)

        if completeness == USAGE_COMPLETENESS_UNAVAILABLE:
            usage_data = None

        return AgentRunUsageSnapshot(
            execution_id=execution_id,
            agent_run_id=agent_run_id,
            run_completed=error is None,
            usage_data=usage_data,
            usage_completeness=completeness,
            provider_response_count=response_count,
            provider_responses=tuple(responses[:MAX_PROVIDER_RESPONSES_IN_SNAPSHOT]),
            provider_responses_truncated_count=truncated,
            retry_prompt_count=retry_prompt_count,
        )
    except Exception:  # pragma: no cover - snapshot must never mask the error
        return AgentRunUsageSnapshot(
            execution_id=execution_id,
            agent_run_id=agent_run_id,
            run_completed=error is None,
            usage_data=None,
            usage_completeness=(
                USAGE_COMPLETENESS_COMPLETE
                if error is None
                else USAGE_COMPLETENESS_UNAVAILABLE
            ),
            provider_response_count=0,
            provider_responses=(),
            provider_responses_truncated_count=0,
            retry_prompt_count=0,
        )


def _classify_completeness(
    error: BaseException | None,
    response_count: int,
) -> str:
    """complete | partial | unavailable for the CONFIRMED usage only.

    - No error: every request completed -> complete.
    - Error with zero observed responses: nothing confirmed -> unavailable.
    - Error with responses observed: complete unless the error is a
      transport-style failure raised while a request was in flight (the
      in-flight request's billing outcome is unknowable) -> partial.
    """
    if error is None:
        return USAGE_COMPLETENESS_COMPLETE
    if response_count == 0:
        return USAGE_COMPLETENESS_UNAVAILABLE
    if _is_transport_in_doubt(error):
        return USAGE_COMPLETENESS_PARTIAL
    return USAGE_COMPLETENESS_COMPLETE


def _is_transport_in_doubt(error: BaseException) -> bool:
    """Conservative type/name check — never inspects exception text."""
    if isinstance(error, ModelAPIError):
        return True
    name = type(error).__name__.lower()
    return any(
        marker in name
        for marker in (
            "timeout",
            "connection",
            "network",
            "reset",
            "stream",
            "incompleterequest",
        )
    )


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


def _attach_failed_run_usage(exc: BaseException, agent_run: Any) -> None:
    """Stash confirmed RunUsage on the original exception; never wrap it.

    Only attaches when the AgentRun already received at least one provider
    request. Pre-provider failures stay unadorned so callers cannot invent
    token/request counts.
    """
    if agent_run is None:
        return
    usage = getattr(agent_run, "usage", None)
    if usage is None:
        return
    if not isinstance(usage, RunUsage) and callable(usage):
        usage = usage()
    if not isinstance(usage, RunUsage):
        return
    if int(getattr(usage, "requests", 0) or 0) <= 0:
        return
    try:
        exc.__dict__["_claread_run_usage"] = build_usage_metadata(usage)
    except Exception:
        return


def extract_run_usage(result: Any) -> dict[str, object] | None:
    """Extract provider usage metadata; emit Reader usage-presence diagnostics.

    Diagnostics fire only when ``current_execution()`` is bound (Reader worker
    scope). Non-Reader callers keep pre-O2 extract-only behaviour.

    Failed Daily runs (no Reader execution scope) may carry confirmed usage on
    the original exception via ``_claread_run_usage``. Never logs prompt,
    article text, raw provider payloads, or secrets.
    """
    if isinstance(result, BaseException):
        usage_data = result.__dict__.get("_claread_run_usage")
        if isinstance(usage_data, dict) and usage_data:
            return usage_data
        return None
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
