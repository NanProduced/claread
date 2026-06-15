"""Ask Claread agent invocation helpers — thin wrappers around agent/model resolution.

This module centralises the repeated "resolve agent + model", "run replan",
and "stream lifecycle" patterns that appear in both the primary stream and
retry paths of service.py.  It does NOT own business logic, branching, or
persistence.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from pydantic_ai import Agent

from app.agents.reader_ask_agent import (
    ReaderAskAgentDeps,
    build_reader_ask_prompt,
    get_reader_ask_agent,
)
from app.config.settings import get_settings
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.router import build_model_for_route
from app.llm.routes import (
    MODEL_ROUTE_READER_ASK,
    MODEL_ROUTE_READER_ASK_PLANNER,
    MODEL_ROUTE_READER_ASK_REPLAN,
)
from app.llm.types import ModelSelection, ResolvedModelConfig, RunModelSettings
from app.services.reader_ask import agent_runner as agent_runner_svc
from app.services.reader_ask import config as cfg
from app.services.reader_ask import stream_events as stream_events_svc

# Re-export types needed by service.py so it doesn't import agent_runner directly.
AgentStreamRuntime = agent_runner_svc.AgentStreamRuntime


@dataclass(slots=True, frozen=True)
class ResolvedReaderAskAgent:
    """Agent + model pair resolved for the READER_ASK route."""

    agent: Agent[ReaderAskAgentDeps, str]
    model: Any
    model_config: ResolvedModelConfig | None


def resolve_reader_ask_agent(
    model_selection: ModelSelection | None = None,
) -> ResolvedReaderAskAgent:
    """Resolve the reader-ask agent and its model for the current settings.

    Raises ``RuntimeError`` if the model route is not configured.
    """
    agent = get_reader_ask_agent()
    model, model_config = build_model_for_route(
        get_settings(),
        MODEL_ROUTE_READER_ASK,
        model_selection,
    )
    if model is None:
        raise RuntimeError("model route is not configured: reader_ask")
    return ResolvedReaderAskAgent(agent=agent, model=model, model_config=model_config)


async def run_reader_ask_replan(
    *,
    replan_deps: ReaderAskAgentDeps,
    replan_max_output: int,
    route_settings: RunModelSettings,
    model_selection: ModelSelection | None = None,
) -> str:
    """Run a non-streaming replan with the given deps and return the result text.

    This encapsulates the repeated pattern of resolving a fresh agent/model,
    constructing a capped ``RunModelSettings``, and calling ``agent.run()``.
    """
    replan_resolved = resolve_reader_ask_agent(model_selection)
    replan_model, replan_model_config = build_reader_ask_replan_model_route(
        model_selection
    )
    if replan_model is None:
        raise RuntimeError("model route is not configured: reader_ask_replan")
    assert_real_llm_allowed(
        "app.services.reader_ask.agent_invocation.run_reader_ask_replan",
        model_config=replan_model_config,
    )
    replan_route = route_settings.with_max_tokens(
        min(
            route_settings.max_tokens or cfg.DEFAULT_MAX_OUTPUT_TOKENS,
            replan_max_output,
        )
    )
    result = await replan_resolved.agent.run(
        build_reader_ask_prompt(replan_deps),
        deps=replan_deps,
        model=replan_model,
        model_settings=replan_route.to_pydantic_ai(),
    )
    return str(result.output).strip() if result.output else ""


# ---------------------------------------------------------------------------
# Stream lifecycle facade
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class ReaderAskStreamSseEvent:
    """A single SSE frame produced during the agent stream."""

    encoded_sse: str


@dataclass(slots=True, frozen=True)
class ReaderAskStreamCompleted:
    """The agent stream has finished — carry the outcome and runtime."""

    outcome: agent_runner_svc.AgentStreamOutcome
    stream_runtime: agent_runner_svc.AgentStreamRuntime


async def stream_reader_ask_agent_run(
    *,
    agent: Any,
    deps: ReaderAskAgentDeps,
    model: Any,
    route_settings: RunModelSettings,
    assistant_message_id: str,
    model_config: ResolvedModelConfig | None = None,
    checkpoint_flush: Callable[..., Awaitable[None]] | None = None,
) -> AsyncIterator[ReaderAskStreamSseEvent | ReaderAskStreamCompleted]:
    """Run the agent stream lifecycle and yield SSE events + final outcome.

    This encapsulates the repeated pattern of starting the stream, consuming
    events, encoding SSE, awaiting the producer task, finishing the stream,
    and handling interrupted events.  The caller simply iterates and dispatches
    based on the yielded type.
    """
    producer_task, stream_runtime = agent_runner_svc.start_reader_ask_agent_stream(
        agent=agent,
        deps=deps,
        model=model,
        route_settings=route_settings,
        assistant_message_id=assistant_message_id,
        model_config=model_config,
        checkpoint_flush=checkpoint_flush,
    )
    try:
        async for event_name, event_payload in agent_runner_svc.stream_reader_ask_events(
            event_queue=deps.event_queue,
            producer_done=stream_runtime.producer_done,
        ):
            yield ReaderAskStreamSseEvent(
                encoded_sse=stream_events_svc.encode_sse(event_name, event_payload),
            )
    finally:
        await producer_task

    stream_outcome, interrupted_event = agent_runner_svc.finish_reader_ask_agent_stream(
        runtime=stream_runtime,
        assistant_message_id=assistant_message_id,
    )

    # Round 6: propagate first_token_at from stream runtime to deps state
    if stream_runtime.first_token_at is not None:
        deps.state.first_token_at = stream_runtime.first_token_at
    if interrupted_event is not None:
        yield ReaderAskStreamSseEvent(
            encoded_sse=stream_events_svc.encode_sse(interrupted_event[0], interrupted_event[1]),
        )

    yield ReaderAskStreamCompleted(outcome=stream_outcome, stream_runtime=stream_runtime)


# ---------------------------------------------------------------------------
# Replan event facade
# ---------------------------------------------------------------------------

def build_reader_ask_replan_event(
    *,
    final_content_md: str,
    planning_snapshot: Any,
    assistant_message_id: str,
    planner_route: str = "planner_first",
    runtime_state: Any | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Check if replan should be triggered and return the replan.started event.

    Thin wrapper around ``agent_runner_svc.build_replan_event`` so that
    service.py does not need to import agent_runner directly.
    """
    return agent_runner_svc.build_replan_event(
        final_content_md=final_content_md,
        planning_snapshot=planning_snapshot,
        assistant_message_id=assistant_message_id,
        planner_route=planner_route,
        runtime_state=runtime_state,
    )


# ---------------------------------------------------------------------------
# Planner model route callback facade
# ---------------------------------------------------------------------------

def build_reader_ask_planner_model_route(
    model_selection: ModelSelection | None = None,
) -> tuple[Any, ResolvedModelConfig | None]:
    """Return the model and config for the READER_ASK_PLANNER route.

    Thin wrapper so service.py does not need to import
    ``build_model_for_route`` / ``MODEL_ROUTE_READER_ASK_PLANNER`` directly.
    """
    return build_model_for_route(
        get_settings(),
        MODEL_ROUTE_READER_ASK_PLANNER,
        model_selection,
    )


def build_reader_ask_replan_model_route(
    model_selection: ModelSelection | None = None,
) -> tuple[Any, ResolvedModelConfig | None]:
    return build_model_for_route(
        get_settings(),
        MODEL_ROUTE_READER_ASK_REPLAN,
        model_selection,
    )


def make_reader_ask_planner_model_route_cb(
    model_selection: ModelSelection | None = None,
) -> Callable[[], tuple[Any, ResolvedModelConfig | None]]:
    return partial(build_reader_ask_planner_model_route, model_selection)
