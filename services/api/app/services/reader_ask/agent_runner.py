"""Agent runner: stream execution, replan detection, and outcome handling.

This module owns the agent run lifecycle — starting the stream, consuming
events, detecting degenerate answers, and assembling the final outcome.
It does NOT touch repo/credits/persistence; checkpoint flushing is injected
via an optional callback.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import (
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)

from app.agents.reader_ask_agent import (
    ReaderAskAgentDeps,
    ReaderAskRuntimeState,
    build_reader_ask_prompt,
)
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.types import ResolvedModelConfig, RunModelSettings
from app.services.reader_ask import stream_events as stream_events_svc
from app.workflow.tracing import build_usage_metadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Degenerate answer detection
# ---------------------------------------------------------------------------

_REFUSAL_PATTERNS: tuple[str, ...] = (
    "i cannot",
    "i can't",
    "i'm unable",
    "i am unable",
    "无法回答",
    "不能回答",
    "无法提供",
    "我无法",
    "我不能",
    "as an ai",
    "as a language model",
    "no information",
    "没有相关信息",
    "没有足够的信息",
    "not enough information",
    "i don't have",
    "i do not have",
)


def is_degenerate_answer(content: str) -> bool:
    """Determine if an answer is degenerate (empty, refusal, or clearly invalid).

    This replaces the previous `len(content) < 20` heuristic with pattern-based
    detection that distinguishes between:
    - Short but valid answers (e.g. "Yes.", "Present perfect.") -> NOT degenerate
    - Empty/near-empty answers -> degenerate
    - Refusal/non-answer patterns -> degenerate
    - Very short answers that are not recognizable words -> degenerate
    """
    stripped = content.strip()
    if not stripped:
        return True
    lower = stripped.lower()
    if any(pattern in lower for pattern in _REFUSAL_PATTERNS):
        return True
    if len(stripped) < 5:
        if stripped.rstrip(".!?,;:") and len(stripped.rstrip(".!?,;:")) <= 3:
            has_cjk = any("\u4e00" <= c <= "\u9fff" for c in stripped)
            has_alpha_word = any(c.isalpha() for c in stripped)
            if has_cjk or has_alpha_word:
                return False
        return True
    return False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AgentStreamOutcome:
    content_md: str
    usage_summary: dict[str, Any] | None
    interrupted: bool
    interruption_detail: str | None = None


@dataclass(slots=True)
class AgentStreamRuntime:
    content_parts: list[str] = field(default_factory=list)
    usage_summary: dict[str, Any] | None = None
    producer_done: asyncio.Event = field(default_factory=asyncio.Event)
    producer_error: Exception | None = None
    emitted_text: str = ""
    emitted_reasoning: str = ""
    reasoning_started: bool = False
    reasoning_active: bool = False
    # Set by the producer task at the end of the `agent.run_stream(...)`
    # context block.  ``finish_reader_ask_agent_stream`` reads this to use
    # the model's authoritative output as the final
    # ``outcome.content_md``.  ``emitted_text`` retains the raw stream so
    # the checkpoint writer and eval can detect lost-delta cases.
    producer_result: Any | None = None
    # The authoritative final output captured from ``await result.get_output()``
    # after stream completion.  This is the primary source for
    # ``outcome.content_md``; ``producer_result.output`` is NOT used because
    # pydantic-ai 1.73+ ``StreamedRunResult`` exposes ``get_output()`` rather
    # than an ``output`` property.
    authoritative_output: str | None = None
    # Degenerate-answer metadata: set by ``build_replan_event`` when
    # ``planner_route == "agent_loop_first"`` so that the caller can
    # observe the detection without triggering a replan.
    degenerate_detected: bool = False
    degenerate_reason: str | None = None
    # Round 6 — observability: first token latency.
    # ISO 8601 timestamp of the first text delta emitted.
    first_token_at: str | None = None


# ---------------------------------------------------------------------------
# Reasoning settings helper
# ---------------------------------------------------------------------------

def prepare_stream_model_settings(
    route_settings: RunModelSettings,
    *,
    model_config: ResolvedModelConfig | None = None,
) -> RunModelSettings:
    """Augment route settings for streaming.

    The DashScope OpenAI-compat endpoint needs an explicit
    ``X-DashScope-SSE`` header and ``incremental_output=True`` to
    stream deltas (the native adapter does not need either, since
    it streams via ``AioGeneration`` with ``incremental_output=True``
    baked in).
    """
    extra_body = dict(route_settings.extra_body or {})
    extra_headers = dict(route_settings.extra_headers or {})

    if (
        model_config is not None
        and model_config.adapter == "openai_compatible"
        and "dashscope.aliyuncs.com" in model_config.base_url.lower()
    ):
        extra_headers.setdefault("X-DashScope-SSE", "enable")
        extra_body.setdefault("incremental_output", True)
    return RunModelSettings(
        max_tokens=route_settings.max_tokens,
        temperature=route_settings.temperature,
        top_p=route_settings.top_p,
        timeout=route_settings.timeout,
        parallel_tool_calls=route_settings.parallel_tool_calls,
        seed=route_settings.seed,
        presence_penalty=route_settings.presence_penalty,
        frequency_penalty=route_settings.frequency_penalty,
        stop_sequences=route_settings.stop_sequences,
        extra_headers=extra_headers or None,
        extra_body=extra_body or None,
    )


# ---------------------------------------------------------------------------
# Agent stream lifecycle
# ---------------------------------------------------------------------------

def build_replan_event(
    *,
    final_content_md: str,
    planning_snapshot: Any,
    assistant_message_id: str,
    planner_route: str = "planner_first",
    runtime_state: ReaderAskRuntimeState | AgentStreamRuntime | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Check if replan should be triggered and return the replan.started event.

    Returns the (event_name, event_payload) tuple if replan is triggered,
    None otherwise.

    When ``planner_route == "agent_loop_first"``, degenerate answers are
    detected and recorded as metadata on ``runtime_state`` but no replan
    event is returned — the agent loop continues without interruption.
    For all other routes (including the default ``"planner_first"``), the
    existing replan logic applies.

    The caller is responsible for yielding the event via SSE — this function
    does NOT put anything on the event_queue because it is called after the
    stream consumer has already exited.
    """
    is_degenerate = (
        is_degenerate_answer(final_content_md)
        and planning_snapshot is not None
        and planning_snapshot.clarification_mode == "none"
        and planning_snapshot.clarification_only is False
    )

    if planner_route == "agent_loop_first":
        # For agent_loop_first, planning_snapshot is None so the
        # compound degenerate check above will always be False.
        # Use a simpler check that only looks at the answer content.
        if is_degenerate_answer(final_content_md):
            logger.warning(
                "reader_ask_degenerate_detected_no_replan: Degenerate answer detected "
                "(%d chars) in agent_loop_first route — recording metadata, skipping replan",
                len(final_content_md.strip()),
            )
            if runtime_state is not None:
                runtime_state.degenerate_detected = True
                runtime_state.degenerate_reason = "degenerate_answer"
        return None

    # planner_first (default) — existing replan logic
    if is_degenerate:
        logger.warning(
            "reader_ask_replan_triggered: Degenerate answer detected (%d chars), attempting replan",
            len(final_content_md.strip()),
        )
        return (
            stream_events_svc.EVENT_REPLAN_STARTED,
            stream_events_svc.replan_started_payload(assistant_message_id, "degenerate_answer"),
        )
    return None


async def _start_reasoning(
    *,
    runtime: AgentStreamRuntime,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    assistant_message_id: str,
) -> None:
    if runtime.reasoning_active:
        return
    runtime.reasoning_started = True
    runtime.reasoning_active = True
    await event_queue.put(
        (
            stream_events_svc.EVENT_REASONING_STARTED,
            stream_events_svc.reasoning_started_payload(assistant_message_id),
        )
    )


async def _append_reasoning_delta(
    *,
    runtime: AgentStreamRuntime,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    assistant_message_id: str,
    reasoning_delta: str,
) -> None:
    if not reasoning_delta:
        return
    runtime.emitted_reasoning += reasoning_delta
    await event_queue.put(
        (
            stream_events_svc.EVENT_REASONING_DELTA,
            stream_events_svc.reasoning_delta_payload(assistant_message_id, reasoning_delta),
        )
    )


async def _complete_reasoning(
    *,
    runtime: AgentStreamRuntime,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    assistant_message_id: str,
) -> None:
    if not runtime.reasoning_active:
        return
    runtime.reasoning_active = False
    await event_queue.put(
        (
            stream_events_svc.EVENT_REASONING_COMPLETED,
            stream_events_svc.reasoning_completed_payload(assistant_message_id),
        )
    )


async def _append_text_delta(
    *,
    runtime: AgentStreamRuntime,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    assistant_message_id: str,
    text_delta: str,
) -> None:
    if not text_delta:
        return
    # Round 6: record first token latency
    if not runtime.emitted_text:
        from datetime import UTC, datetime

        runtime.first_token_at = datetime.now(UTC).isoformat()
    runtime.emitted_text += text_delta
    runtime.content_parts.append(text_delta)
    await event_queue.put(
        (
            stream_events_svc.EVENT_MESSAGE_DELTA,
            stream_events_svc.message_delta_payload(assistant_message_id, text_delta),
        )
    )


async def _consume_response_snapshot(
    *,
    response: Any,
    runtime: AgentStreamRuntime,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    assistant_message_id: str,
) -> None:
    thinking_text = response.thinking or ""
    logger.debug(
        "reasoning_snapshot thinking_len=%d emitted_len=%d",
        len(thinking_text),
        len(runtime.emitted_reasoning),
    )
    if thinking_text and not runtime.reasoning_started:
        await _start_reasoning(
            runtime=runtime,
            event_queue=event_queue,
            assistant_message_id=assistant_message_id,
        )

    if thinking_text.startswith(runtime.emitted_reasoning):
        reasoning_delta = thinking_text[len(runtime.emitted_reasoning):]
    else:
        reasoning_delta = thinking_text
    await _append_reasoning_delta(
        runtime=runtime,
        event_queue=event_queue,
        assistant_message_id=assistant_message_id,
        reasoning_delta=reasoning_delta,
    )

    text_value = response.text or ""
    if text_value.startswith(runtime.emitted_text):
        text_delta = text_value[len(runtime.emitted_text):]
    else:
        text_delta = text_value
    await _append_text_delta(
        runtime=runtime,
        event_queue=event_queue,
        assistant_message_id=assistant_message_id,
        text_delta=text_delta,
    )


async def _consume_raw_stream_event(
    *,
    event: Any,
    runtime: AgentStreamRuntime,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    assistant_message_id: str,
) -> None:
    if isinstance(event, PartStartEvent):
        if isinstance(event.part, ThinkingPart):
            logger.info("reasoning_part_start content_len=%d", len(event.part.content))
            await _start_reasoning(
                runtime=runtime,
                event_queue=event_queue,
                assistant_message_id=assistant_message_id,
            )
            await _append_reasoning_delta(
                runtime=runtime,
                event_queue=event_queue,
                assistant_message_id=assistant_message_id,
                reasoning_delta=event.part.content,
            )
            return
        if isinstance(event.part, TextPart):
            await _append_text_delta(
                runtime=runtime,
                event_queue=event_queue,
                assistant_message_id=assistant_message_id,
                text_delta=event.part.content,
            )
            return

    if isinstance(event, PartDeltaEvent):
        if isinstance(event.delta, ThinkingPartDelta):
            if event.delta.content_delta and not runtime.reasoning_active:
                await _start_reasoning(
                    runtime=runtime,
                    event_queue=event_queue,
                    assistant_message_id=assistant_message_id,
                )
            await _append_reasoning_delta(
                runtime=runtime,
                event_queue=event_queue,
                assistant_message_id=assistant_message_id,
                reasoning_delta=event.delta.content_delta or "",
            )
            return
        if isinstance(event.delta, TextPartDelta):
            await _append_text_delta(
                runtime=runtime,
                event_queue=event_queue,
                assistant_message_id=assistant_message_id,
                text_delta=event.delta.content_delta or "",
            )
            return

    if isinstance(event, PartEndEvent) and isinstance(event.part, ThinkingPart):
        await _complete_reasoning(
            runtime=runtime,
            event_queue=event_queue,
            assistant_message_id=assistant_message_id,
        )


def _stream_response_from_result(result: Any) -> Any | None:
    result_dict = getattr(result, "__dict__", None)
    if not isinstance(result_dict, dict):
        return None
    stream_response = result_dict.get("_stream_response")
    if stream_response is None or not hasattr(stream_response, "__aiter__"):
        return None
    return stream_response


async def _mark_stream_result_completed(result: Any, runtime: AgentStreamRuntime) -> None:
    """Finalize the stream result and capture the authoritative output.

    First, the result is marked as completed (via ``_marked_completed`` or
    ``get_output()``).  Then the final output is captured from
    ``await result.get_output()`` and stored on
    ``runtime.authoritative_output``.  This is the primary source for
    ``outcome.content_md`` in ``finish_reader_ask_agent_stream``.

    Completion failures (e.g. ``ModelHTTPError``, auth errors) are **not**
    swallowed — they propagate to the caller so the outer producer error
    path can record the failure via ``runtime.producer_error``.  Only
    ``AttributeError`` (method signature mismatch) is silently skipped.
    """
    mark_completed = getattr(result, "_marked_completed", None)
    if callable(mark_completed):
        try:
            await mark_completed(result.response)
        except AttributeError:
            # Method exists but signature mismatch — not a real failure.
            pass

    # Capture the authoritative output from get_output().  When
    # ``_marked_completed`` was not available, this call also finalizes
    # the result.
    get_output = getattr(result, "get_output", None)
    if callable(get_output):
        output = await get_output()
        text = str(output).strip() if output is not None else None
        runtime.authoritative_output = text or None


async def _replay_missed_reasoning(
    result: Any,
    runtime: AgentStreamRuntime,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    assistant_message_id: str,
) -> None:
    """Fallback: emit synthetic reasoning events from the final response.

    pydantic-ai's agent-level stream (with ``output_type=str``) does not
    surface ``PartStartEvent`` for the initial ``ThinkingPart`` and
    ``ThinkingPartDelta`` events emitted by ``FunctionModel``.  The
    ``ThinkingPart`` IS preserved on ``result.response.parts`` — read it
    back and emit a single ``reasoning.started``/``reasoning.delta``/
    ``reasoning.completed`` triple if we did not already stream the
    reasoning incrementally.
    """
    if runtime.reasoning_started:
        return
    response = getattr(result, "response", None)
    if response is None:
        return
    parts = getattr(response, "parts", None) or []
    for part in parts:
        if isinstance(part, ThinkingPart) and part.content:
            await _start_reasoning(
                runtime=runtime,
                event_queue=event_queue,
                assistant_message_id=assistant_message_id,
            )
            await _append_reasoning_delta(
                runtime=runtime,
                event_queue=event_queue,
                assistant_message_id=assistant_message_id,
                reasoning_delta=part.content,
            )
            await _complete_reasoning(
                runtime=runtime,
                event_queue=event_queue,
                assistant_message_id=assistant_message_id,
            )
            return


def _resolve_authoritative_final_text(runtime: AgentStreamRuntime) -> str | None:
    """Return the authoritative final text, or None.

    The primary source is ``runtime.authoritative_output``, which is
    captured from ``await result.get_output()`` during stream completion.
    This avoids relying on ``result.output`` which does not exist on
    pydantic-ai 1.73+ ``StreamedRunResult``.
    """
    if runtime.authoritative_output is not None:
        return runtime.authoritative_output
    return None


def start_reader_ask_agent_stream(
    *,
    agent: Any,
    deps: ReaderAskAgentDeps,
    model: Any,
    route_settings: RunModelSettings,
    assistant_message_id: str,
    model_config: ResolvedModelConfig | None = None,
    checkpoint_flush: Callable[..., Awaitable[None]] | None = None,
) -> tuple[asyncio.Task[None], AgentStreamRuntime]:
    """Start the agent stream as a background task.

    Args:
        model_config: Resolved model config (used by ``prepare_stream_model_settings``
            to decide whether the compat DashScope SSE toggle is needed).  When
            ``None`` (e.g. tests that bypass the router) no extra header is added.
        checkpoint_flush: Optional async callback for flushing stream checkpoints.
            Called as ``await checkpoint_flush(runtime, force=False)`` after each
            response iteration and ``await checkpoint_flush(runtime, force=True)``
            on completion or error.  When *None*, no checkpoint flushing occurs.
    """
    event_queue = deps.event_queue
    runtime = AgentStreamRuntime()

    async def run_agent_stream() -> None:
        try:
            assert_real_llm_allowed(
                "app.services.reader_ask.agent_runner.start_reader_ask_agent_stream",
                model_config=model_config,
            )
            async with agent.run_stream(
                build_reader_ask_prompt(deps),
                deps=deps,
                model=model,
                model_settings=prepare_stream_model_settings(
                    route_settings,
                    model_config=model_config,
                ).to_pydantic_ai(),
            ) as result:
                stream_response = _stream_response_from_result(result)
                logger.info("reasoning_stream_path raw_stream=%s", stream_response is not None)
                if stream_response is not None:
                    async for event in stream_response:
                        await _consume_raw_stream_event(
                            event=event,
                            runtime=runtime,
                            event_queue=event_queue,
                            assistant_message_id=assistant_message_id,
                        )
                        if checkpoint_flush is not None:
                            await checkpoint_flush(runtime, force=False)
                    await _mark_stream_result_completed(result, runtime)
                    await _replay_missed_reasoning(
                        result, runtime, event_queue, assistant_message_id,
                    )
                else:
                    async for response, _last in result.stream_responses(debounce_by=None):
                        await _consume_response_snapshot(
                            response=response,
                            runtime=runtime,
                            event_queue=event_queue,
                            assistant_message_id=assistant_message_id,
                        )
                        if checkpoint_flush is not None:
                            await checkpoint_flush(runtime, force=False)
                    # Finalize the stream and capture the authoritative
                    # output from ``await result.get_output()``.
                    await _mark_stream_result_completed(result, runtime)
                    await _replay_missed_reasoning(
                        result, runtime, event_queue, assistant_message_id,
                    )

                await _complete_reasoning(
                    runtime=runtime,
                    event_queue=event_queue,
                    assistant_message_id=assistant_message_id,
                )
                logger.debug(
                    "reasoning_stream_done reasoning_len=%d started=%s",
                    len(runtime.emitted_reasoning),
                    runtime.reasoning_started,
                )
                if checkpoint_flush is not None:
                    await checkpoint_flush(runtime, force=True)
                runtime.usage_summary = build_usage_metadata(result.usage())
                runtime.producer_result = result
        except Exception as exc:
            if checkpoint_flush is not None:
                await checkpoint_flush(runtime, force=True)
            runtime.producer_error = exc
        finally:
            runtime.producer_done.set()

    return asyncio.create_task(run_agent_stream()), runtime


async def stream_reader_ask_events(
    *,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    producer_done: asyncio.Event,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    while not producer_done.is_set() or not event_queue.empty():
        try:
            event_name, event_payload = await asyncio.wait_for(event_queue.get(), timeout=0.1)
        except TimeoutError:
            continue
        yield event_name, event_payload


def finish_reader_ask_agent_stream(
    *,
    runtime: AgentStreamRuntime,
    assistant_message_id: str,
) -> tuple[AgentStreamOutcome, tuple[str, dict[str, Any]] | None]:
    streamed_text = "".join(runtime.content_parts).strip()
    # ``runtime.authoritative_output`` (captured from ``await
    # result.get_output()``) is the authoritative source for the persisted
    # and completed-payload content. ``runtime.emitted_text`` retains the
    # raw stream for the checkpoint writer and for eval detection of
    # lost-delta cases — never overwrite it here.
    authoritative_text = _resolve_authoritative_final_text(runtime)
    final_content_md = authoritative_text if authoritative_text else streamed_text
    if runtime.producer_error is not None:
        if final_content_md:
            return (
                AgentStreamOutcome(
                    content_md=final_content_md,
                    usage_summary=runtime.usage_summary,
                    interrupted=True,
                    interruption_detail=str(runtime.producer_error) or "输出中断",
                ),
                (
                    stream_events_svc.EVENT_MESSAGE_INTERRUPTED,
                    stream_events_svc.message_interrupted_payload(
                        message_id=assistant_message_id,
                        content_md=final_content_md,
                        detail=str(runtime.producer_error) or "输出中断",
                    ),
                ),
            )
        raise runtime.producer_error
    return (
        AgentStreamOutcome(
            content_md=final_content_md,
            usage_summary=runtime.usage_summary,
            interrupted=False,
        ),
        None,
    )
