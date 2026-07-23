"""R4 Ask thinking transport spine (R4-A5-8A1 / A5-8A1R).

Internal-only: captures provider reasoning for a bounded in-memory observer
and emits **safe** analysis-phase runtime events. Never writes reasoning
text, length, hash, or provider payloads into SSE/DTO/DB/logs.

Does **not** import legacy ``reader_ask`` agent_runner.

Multi-turn completeness (A5-8A1R)
--------------------------------
Reasoning collection is deduplicated **per streamed part index lifecycle**,
not with a single global ``saw_reasoning`` flag. After tool results, the
lifecycle set is cleared so a second-round ThinkingPart delivered only via
``PartEnd`` is still observed. ``AnalysisStarted`` / ``AnalysisFinished``
still fire at most once per agent run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    ThinkingPart,
    ThinkingPartDelta,
)

from app.services.reader_record_ask.finalizer import AgentAnswerDraft
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps
from app.services.reader_record_ask.runtime_events import (
    AnalysisFinishedEvent,
    AnalysisStartedEvent,
    RuntimeEvent,
)

# Hard ceiling on characters retained by any ThinkingObserver (host-only).
DEFAULT_THINKING_OBSERVER_CHAR_CAP: int = 8_000


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

    output: AgentAnswerDraft | Any
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


async def run_agent_with_thinking_transport(
    *,
    agent: Agent[ReaderRecordAskDeps, AgentAnswerDraft],
    prompt: str,
    deps: ReaderRecordAskDeps,
    thinking_observer: ThinkingObserver | None = None,
    model: Any = None,
) -> StreamedAgentOutcome:
    """Run the agent capturing thinking privately when streaming is available.

    Prefer ``run_stream_events`` (PartStart/PartDelta/PartEnd ThinkingPart).
    When the model cannot stream (e.g. FunctionModel without
    ``stream_function``), fall back to ``agent.run`` and optionally
    snapshot ThinkingPart from the completed message history.

    Emits only safe ``AnalysisStartedEvent`` / ``AnalysisFinishedEvent`` —
    never raw reasoning on the event sink. Tool calling, validators, and
    structured output use the same agent configuration as ``run()``.
    """
    phase_events: list[RuntimeEvent] = []
    analysis_started = False
    final_output: Any = None
    lifecycle = ThinkingPartLifecycle()

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

    active_model = model if model is not None else getattr(agent, "model", None)
    if not _model_supports_request_stream(active_model):
        result = await agent.run(prompt, deps=deps)
        final_output = result.output
        _notify_observer_from_messages(
            messages=getattr(result, "all_messages", lambda: ())(),
            deps=deps,
            thinking_observer=thinking_observer,
            phase_events=phase_events,
        )
    else:
        async for event in agent.run_stream_events(prompt, deps=deps):
            type_name = type(event).__name__

            if type_name == "AgentRunResultEvent":
                result = getattr(event, "result", None)
                if result is not None:
                    final_output = getattr(result, "output", result)
                continue

            # New model response stream after tools: reset part-index lifecycle.
            if isinstance(event, FunctionToolResultEvent):
                lifecycle.reset_stream()
                continue
            if type_name == "BuiltinToolResultEvent":
                lifecycle.reset_stream()
                continue

            if isinstance(event, PartStartEvent) and isinstance(
                event.part, ThinkingPart
            ):
                piece = lifecycle.on_start(
                    event.index,
                    str(event.part.content) if event.part.content else None,
                )
                _emit_reasoning(piece)
                continue

            if isinstance(event, PartDeltaEvent) and isinstance(
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

            if isinstance(event, PartEndEvent) and isinstance(
                event.part, ThinkingPart
            ):
                piece = lifecycle.on_end(
                    event.index,
                    str(event.part.content) if event.part.content else None,
                )
                _emit_reasoning(piece)
                continue

        if analysis_started:
            _finish_analysis()

    if final_output is None:
        raise RuntimeError("agent run produced no final output")

    if not isinstance(final_output, AgentAnswerDraft):
        final_output = AgentAnswerDraft(
            answer_text=str(final_output),
            cited_evidence_handles=[],
            response_kind="grounded_answer",
        )

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
