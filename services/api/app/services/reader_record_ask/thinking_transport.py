"""R4 Ask thinking transport spine (R4-A5-8A1 / A5-8A1R / A5-8A1R2).

Internal-only: captures provider reasoning for a bounded in-memory observer
and emits **safe** analysis-phase runtime events. Never writes reasoning
text, length, hash, or provider payloads into SSE/DTO/DB/logs.

Does **not** import legacy ``reader_ask`` agent_runner.

Multi-turn completeness (A5-8A1R / A5-8A1R2)
-------------------------------------------
Reasoning collection is deduplicated **per streamed part index lifecycle**,
not with a single global ``saw_reasoning`` flag. The lifecycle set is
cleared on every stable public tool-result boundary so a second-round
ThinkingPart delivered only via ``PartEnd`` is still observed. This covers
function-tool continuation, builtin-tool continuation, output-validator
``ModelRetry`` (``OutputToolResultEvent`` carrying a ``RetryPromptPart``)
and tool-arg ``ModelRetry`` (``FunctionToolResultEvent`` carrying a
``RetryPromptPart``). ``AnalysisStarted`` / ``AnalysisFinished`` still fire
at most once per agent run.

Boundary detection uses the public ``event_kind`` Literal discriminator on
each stream event — not ``type(event).__name__`` string matching, which is
fragile across pydantic-ai versions (e.g. ``BuiltinToolResultEvent`` is
deprecated and its inheritance changed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic_ai import Agent
from pydantic_ai.messages import (
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits
from pydantic_core import from_json

from app.services.reader_record_ask.grounding_validator import (
    AgentAnswerDraftOutput,
)
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
from app.services.reader_record_ask.runtime_events import (
    AnalysisFinishedEvent,
    AnalysisStartedEvent,
    AnswerDeltaEvent,
    RuntimeEvent,
)

# Hard ceiling on characters retained by any ThinkingObserver (host-only).
DEFAULT_THINKING_OBSERVER_CHAR_CAP: int = 8_000

# Stable public ``event_kind`` discriminator values that mark the end of a
# tool/retry boundary — a new model response stream follows each of these,
# so the per-index thinking lifecycle must reset. Using the documented
# Literal discriminator (not type-name guessing) keeps this stable across
# pydantic-ai releases.
_TOOL_RESULT_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "function_tool_result",  # FunctionToolResultEvent (tool return or ModelRetry)
        "output_tool_result",  # OutputToolResultEvent (output-validator ModelRetry)
        "builtin_tool_result",  # BuiltinToolResultEvent (deprecated, still covered)
    }
)


class ThinkingObserver(Protocol):
    """Injected probe for tests / future controlled diagnostics.

    Default production path passes ``None`` — zero collection.
    Implementations must never log, persist, or publish content.
    """

    def on_analysis_started(self) -> None: ...

    def on_reasoning_delta(self, text: str) -> None: ...

    def on_analysis_finished(self) -> None: ...


@dataclass
class BoundedThinkingObserver:
    """In-memory observer with a strict character cap (tests / diagnostics).

    Does not log. Does not persist. Cap truncates silently (no exception).
    """

    char_cap: int = DEFAULT_THINKING_OBSERVER_CHAR_CAP
    chunks: list[str] = field(default_factory=list)
    started: bool = False
    finished: bool = False
    _retained: int = 0

    def on_analysis_started(self) -> None:
        self.started = True

    def on_reasoning_delta(self, text: str) -> None:
        if not text or self._retained >= self.char_cap:
            return
        remaining = self.char_cap - self._retained
        piece = text if len(text) <= remaining else text[:remaining]
        if piece:
            self.chunks.append(piece)
            self._retained += len(piece)

    def on_analysis_finished(self) -> None:
        self.finished = True

    @property
    def text(self) -> str:
        """Joined retained reasoning — host/test only, never SSE/DTO."""
        return "".join(self.chunks)


@dataclass(frozen=True, slots=True)
class StreamedAgentOutcome:
    """Result of a streamed agent run (output + safe phase events)."""

    output: AgentAnswerDraftOutput
    phase_events: tuple[RuntimeEvent, ...] = ()


def _model_supports_request_stream(model: Any) -> bool:
    """Return True when the model can serve streamed requests.

    FunctionModel without ``stream_function`` only supports ``agent.run()``;
    production OpenAI / DashScope native models support streaming.
    """
    stream_fn = getattr(model, "stream_function", None)
    # FunctionModel always has the attribute (may be None).
    if type(model).__name__ == "FunctionModel":
        return stream_fn is not None
    return True


def _notify_observer_from_messages(
    *,
    messages: Any,
    deps: ReaderRecordAskDeps,
    thinking_observer: ThinkingObserver | None,
    phase_events: list[RuntimeEvent],
) -> None:
    """Snapshot fallback: harvest ThinkingPart from completed message history."""
    if thinking_observer is None:
        return
    texts: list[str] = []
    for message in messages or ():
        for part in getattr(message, "parts", ()) or ():
            if isinstance(part, ThinkingPart) and part.content:
                texts.append(str(part.content))
    if not texts:
        return
    event_start = AnalysisStartedEvent()
    phase_events.append(event_start)
    deps.emit_event(event_start)
    thinking_observer.on_analysis_started()
    for text in texts:
        thinking_observer.on_reasoning_delta(text)
    event_end = AnalysisFinishedEvent()
    phase_events.append(event_end)
    deps.emit_event(event_end)
    thinking_observer.on_analysis_finished()


@dataclass
class ThinkingPartLifecycle:
    """Per-index lifecycle for one model response stream (A5-8A1R).

    Indices restart after tool results; clear via :meth:`reset_stream`.
    Only non-empty content marks an index as streamed so PartEnd-only
    rounds still deliver full content.
    """

    streamed_indices: set[int] = field(default_factory=set)

    def reset_stream(self) -> None:
        self.streamed_indices.clear()

    def on_start(self, index: int, content: str | None) -> str | None:
        if content:
            self.streamed_indices.add(index)
            return content
        return None

    def on_delta(self, index: int, content_delta: str | None) -> str | None:
        if content_delta:
            self.streamed_indices.add(index)
            return content_delta
        return None

    def on_end(self, index: int, full_content: str | None) -> str | None:
        if index in self.streamed_indices:
            return None
        if full_content:
            self.streamed_indices.add(index)
            return full_content
        return None


class _AnswerTextStreamer:
    """Partial-JSON answer-block text prefix streamer (R4-A6).

    Accumulates streamed structured-output JSON text and extracts the
    resolved semantic block text via ``pydantic_core.from_json`` with
    ``allow_partial="trailing-strings"`` — no bespoke incremental JSON
    state machine, no regex. Emits only newly resolved prefix increments;
    ``_emitted_len`` is monotonically non-decreasing within one buffer.
    Fed exclusively from ``TextPart`` content: reasoning text and
    tool payloads never enter the buffer.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._emitted_len = 0

    def reset(self) -> None:
        """Drop the buffer at a model-response boundary (tool result)."""
        self._buffer = ""
        self._emitted_len = 0

    def feed(self, text: str) -> str | None:
        """Append a streamed chunk; return newly resolved block text."""
        if not text:
            return None
        self._buffer += text
        try:
            parsed = from_json(self._buffer, allow_partial="trailing-strings")
        except ValueError:
            return None
        if not isinstance(parsed, dict):
            return None
        if parsed.get("response_kind") == "clarification":
            clarification_text = parsed.get("clarification_text")
            if not isinstance(clarification_text, str):
                return None
            answer = clarification_text
        else:
            blocks = parsed.get("answer_blocks")
            if not isinstance(blocks, list):
                return None
            block_texts = [
                block.get("text")
                for block in blocks
                if isinstance(block, dict)
                and isinstance(block.get("text"), str)
            ]
            answer = "\n\n".join(block_texts)
        if len(answer) <= self._emitted_len:
            return None
        delta = answer[self._emitted_len :]
        self._emitted_len = len(answer)
        return delta


async def run_agent_with_thinking_transport(
    *,
    agent: Agent[ReaderRecordAskDeps, AgentAnswerDraftOutput],
    prompt: str,
    deps: ReaderRecordAskDeps,
    thinking_observer: ThinkingObserver | None = None,
    model: Any = None,
    model_settings: ModelSettings | None = None,
    usage_limits: UsageLimits | None = None,
) -> StreamedAgentOutcome:
    """Run the agent capturing thinking privately when streaming is available.

    Prefer ``run_stream_events`` (PartStart/PartDelta/PartEnd ThinkingPart).
    When the model cannot stream (e.g. FunctionModel without
    ``stream_function``), fall back to ``agent.run`` and optionally
    snapshot ThinkingPart from the completed message history.

    Emits only safe ``AnalysisStartedEvent`` / ``AnalysisFinishedEvent``
    plus token-level ``AnswerDeltaEvent`` answer-block text increments (R4-A6,
    streamed TextPart content only) — never raw reasoning on the event
    sink. Tool calling, validators, and structured output use the same
    agent configuration as ``run()``.

    ASK-M1: ``model_settings`` and ``usage_limits`` forward the resolved
    product budget (provider completion cap + host second-layer guard)
    into PydanticAI's ``agent.run`` / ``agent.run_stream_events``. Both
    default to ``None`` (PydanticAI then uses the agent / model default)
    so existing test callers that don't pass them are unaffected.
    """
    phase_events: list[RuntimeEvent] = []
    analysis_started = False
    final_output: Any = None
    lifecycle = ThinkingPartLifecycle()
    answer_streamer = _AnswerTextStreamer()
    answer_streamed_indices: set[int] = set()

    def _ensure_started() -> None:
        nonlocal analysis_started
        if analysis_started:
            return
        analysis_started = True
        event = AnalysisStartedEvent()
        phase_events.append(event)
        deps.emit_event(event)
        if thinking_observer is not None:
            thinking_observer.on_analysis_started()

    def _finish_analysis() -> None:
        if not analysis_started:
            return
        event = AnalysisFinishedEvent()
        phase_events.append(event)
        deps.emit_event(event)
        if thinking_observer is not None:
            thinking_observer.on_analysis_finished()

    def _emit_reasoning(text: str | None) -> None:
        if not text:
            return
        _ensure_started()
        if thinking_observer is not None:
            thinking_observer.on_reasoning_delta(text)

    def _emit_answer_delta(delta: str | None) -> None:
        # Answer text is user-visible output — safe on the event sink.
        # Never derived from ThinkingPart content (isolated feed path).
        if not delta:
            return
        event = AnswerDeltaEvent(delta=delta)
        phase_events.append(event)
        deps.emit_event(event)

    active_model = model if model is not None else getattr(agent, "model", None)
    if not _model_supports_request_stream(active_model):
        result = await agent.run(
            prompt,
            deps=deps,
            model_settings=model_settings,
            usage_limits=usage_limits,
        )
        final_output = result.output
        _notify_observer_from_messages(
            messages=getattr(result, "all_messages", lambda: ())(),
            deps=deps,
            thinking_observer=thinking_observer,
            phase_events=phase_events,
        )
    else:
        async for event in agent.run_stream_events(
            prompt,
            deps=deps,
            model_settings=model_settings,
            usage_limits=usage_limits,
        ):
            event_kind = getattr(event, "event_kind", None)

            if event_kind == "agent_run_result":
                result = getattr(event, "result", None)
                if result is not None:
                    final_output = getattr(result, "output", result)
                continue

            # New model response stream after a tool/retry boundary: reset
            # the per-index thinking lifecycle so a PartEnd-only second round
            # is still observed exactly once. Covers FunctionToolResultEvent
            # (tool return or tool-arg ModelRetry), OutputToolResultEvent
            # (output-validator ModelRetry), and the deprecated
            # BuiltinToolResultEvent — all via the stable event_kind Literal.
            if event_kind in _TOOL_RESULT_EVENT_KINDS:
                lifecycle.reset_stream()
                # R4-A6: a new model response stream follows the boundary —
                # drop any intermediate text so the final answer JSON parses
                # from a clean buffer (indices restart as well).
                answer_streamer.reset()
                answer_streamed_indices.clear()
                continue

            if event_kind == "part_start" and isinstance(
                event.part, ThinkingPart
            ):
                piece = lifecycle.on_start(
                    event.index,
                    str(event.part.content) if event.part.content else None,
                )
                _emit_reasoning(piece)
                continue

            if event_kind == "part_delta" and isinstance(
                event.delta, ThinkingPartDelta
            ):
                piece = lifecycle.on_delta(
                    event.index,
                    (
                        str(event.delta.content_delta)
                        if event.delta.content_delta
                        else None
                    ),
                )
                _emit_reasoning(piece)
                continue

            if event_kind == "part_end" and isinstance(
                event.part, ThinkingPart
            ):
                piece = lifecycle.on_end(
                    event.index,
                    str(event.part.content) if event.part.content else None,
                )
                _emit_reasoning(piece)
                continue

            # R4-A6: answer-block text streaming. TextPart carries streamed
            # structured-output JSON; feed content into the partial parser
            # and emit AnswerDeltaEvent prefix increments. Fully
            # isolated from the ThinkingPart path above: no observer calls,
            # no AnalysisStarted synthesis, no shared content.
            if event_kind == "part_start" and isinstance(event.part, TextPart):
                content = (
                    str(event.part.content) if event.part.content else None
                )
                if content:
                    answer_streamed_indices.add(event.index)
                    _emit_answer_delta(answer_streamer.feed(content))
                continue

            if event_kind == "part_delta" and isinstance(
                event.delta, TextPartDelta
            ):
                piece = (
                    str(event.delta.content_delta)
                    if event.delta.content_delta
                    else None
                )
                if piece:
                    answer_streamed_indices.add(event.index)
                    _emit_answer_delta(answer_streamer.feed(piece))
                continue

            if event_kind == "part_end" and isinstance(event.part, TextPart):
                # PartEnd-only delivery: full content without prior
                # start/delta for this index.
                if (
                    event.index not in answer_streamed_indices
                    and event.part.content
                ):
                    answer_streamed_indices.add(event.index)
                    _emit_answer_delta(
                        answer_streamer.feed(str(event.part.content))
                    )
                continue

        if analysis_started:
            _finish_analysis()

    if final_output is None:
        raise RuntimeError("agent run produced no final output")

    if not isinstance(final_output, AgentAnswerDraftOutput):
        raise TypeError("agent transport received an invalid structured output")

    return StreamedAgentOutcome(
        output=final_output,
        phase_events=tuple(phase_events),
    )


__all__ = [
    "DEFAULT_THINKING_OBSERVER_CHAR_CAP",
    "BoundedThinkingObserver",
    "StreamedAgentOutcome",
    "ThinkingObserver",
    "ThinkingPartLifecycle",
    "run_agent_with_thinking_transport",
]
