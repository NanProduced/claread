"""R4 Ask thinking transport spine (R4-A5-8A1 / A5-8A1R / A5-8A1R2).

Internal-only: captures provider reasoning for a bounded in-memory observer
and emits **safe** analysis-phase runtime events. Never writes raw
reasoning text, length, hash, or provider payloads into SSE/DTO/DB/logs.

Learner reasoning (ASK-LEARNER-REASONING-PROJECTOR-R1)
-----------------------------------------------------
The observer injection point is the only structural path by which provider
reasoning can leave this module. The default production path discards at
ingress (feature flag OFF). When the flag is ON, a turn-local learner-
reasoning sidecar may buffer raw text in memory and publish only Host-
validated Chinese stage summaries via ``agentic.learner_reasoning.snapshot``.
Raw reasoning remains forbidden from SSE/DTO/DB/logs/telemetry.

Does **not** import legacy ``reader_ask`` agent_runner.

Multi-turn completeness (A5-8A1R / A5-8A1R2)
-------------------------------------------
Reasoning collection is deduplicated **per streamed part index lifecycle**,
not with a single global ``saw_reasoning`` flag. The lifecycle set is
cleared on every stable public tool-result boundary so a second-round
ThinkingPart delivered only via ``PartEnd`` is still observed. This covers
function-tool continuation, builtin-tool continuation, output-validator
``ModelRetry`` (``OutputToolResultEvent`` with ``RetryPromptPart``; the
legacy twin ``FunctionToolResultEvent`` is skipped once), and tool-arg
``ModelRetry`` (``FunctionToolResultEvent`` with ``RetryPromptPart``).
Successful output-tool ``ToolReturnPart`` does not advance generation.
``AnalysisStarted`` / ``AnalysisFinished`` still fire at most once per run.

Boundary detection uses the public ``event_kind`` Literal discriminator on
each stream event — not ``type(event).__name__`` string matching, which is
fragile across pydantic-ai versions (e.g. ``BuiltinToolResultEvent`` is
deprecated and its inheritance changed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic_ai import Agent
from pydantic_ai.messages import (
    BuiltinToolResultEvent,
    FunctionToolResultEvent,
    OutputToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
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
    AnswerPreviewResetEvent,
    RuntimeEvent,
)

# Hard ceiling on characters retained by any ThinkingObserver (host-only).
DEFAULT_THINKING_OBSERVER_CHAR_CAP: int = 8_000


class ThinkingObserver(Protocol):
    """Injected probe receiving raw provider reasoning chunks.

    Raw reasoning is never safe content. Implementations must never log,
    persist, or publish raw content. Production may wire a learner-
    reasoning sidecar that buffers turn-local raw text and publishes
    only Host-validated Chinese stage summaries via
    ``agentic.learner_reasoning.snapshot``. The default production path
    when the feature flag is OFF discards at ingress (zero collection).
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
    """Incremental answer-block text streamer (R4-A6 / ASK-TURN-LIFECYCLE R3).

    Accumulates streamed structured-output JSON text and extracts the
    resolved semantic block text via ``pydantic_core.from_json`` with
    ``allow_partial="trailing-strings"`` — no bespoke incremental JSON
    state machine, no regex. Emits only newly resolved prefix increments;
    ``_emitted_len`` is monotonically non-decreasing within one buffer.
    Fed exclusively from ``TextPart`` content: reasoning text and
    tool payloads never enter the buffer.

    R3 optimization: block-aware incremental extraction. Instead of
    re-joining all block texts with ``\\n\\n`` on every chunk (O(n) per
    chunk → O(n²) total), the scanner tracks per-block emission state and
    only emits deltas from the last (growing) block. Earlier blocks are
    stable once later blocks appear in the partial parse, so the join
    is reconstructed incrementally rather than recomputed. The
    ``from_json`` parse remains O(buffer) but is executed in Rust
    (pydantic_core) and is fast enough for 30K answers at ~4K chunks.
    """

    def __init__(self) -> None:
        self._buffer = ""
        # Total emitted character count (monotonic; backward-compat with
        # existing tests that introspect ``_emitted_len``).
        self._emitted_len = 0
        # Block-aware incremental state (R3).
        # _emitted_block_texts: texts of blocks already fully emitted.
        # _last_block_emitted_len: char count emitted from the current
        #   (last) block. When a new block appears, the previous last
        #   block is sealed and a separator is emitted before the new
        #   block's text.
        self._emitted_block_texts: list[str] = []
        self._last_block_emitted_len: int = 0
        # Clarification mode: single-block shortcut. When True, the
        # answer is ``clarification_text`` (not ``answer_blocks``).
        self._is_clarification: bool = False

    def reset(self) -> None:
        """Drop the buffer at a model-response boundary (tool result)."""
        self._buffer = ""
        self._emitted_len = 0
        self._emitted_block_texts = []
        self._last_block_emitted_len = 0
        self._is_clarification = False

    def feed(self, text: str) -> str | None:
        """Append a streamed chunk; return newly resolved block text.

        The delta is computed incrementally: only the last block's new
        text (or a newly appeared block) contributes to the returned
        delta. The full ``\\n\\n``-joined answer is never reconstructed
        on the hot path.
        """
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
            return self._emit_clarification(clarification_text)

        blocks = parsed.get("answer_blocks")
        if not isinstance(blocks, list):
            return None
        # Extract text from each block that has a string ``text`` field.
        # Blocks without a string ``text`` are treated as not-yet-resolved
        # (partial parse may yield incomplete trailing blocks).
        block_texts: list[str] = []
        for block in blocks:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block_texts.append(block["text"])
        if not block_texts:
            return None
        return self._emit_blocks(block_texts)

    def _emit_clarification(self, text: str) -> str | None:
        """Single-block clarification: emit delta beyond what we've sent."""
        if not self._is_clarification:
            # Transitioning from answer-blocks mode (or initial) to
            # clarification. Reset block state to avoid mixed emission.
            self._emitted_block_texts = []
            self._last_block_emitted_len = 0
            self._is_clarification = True
        if len(text) <= self._last_block_emitted_len:
            return None
        delta = text[self._last_block_emitted_len :]
        self._last_block_emitted_len = len(text)
        self._emitted_len += len(delta)
        return delta

    def _emit_blocks(self, block_texts: list[str]) -> str | None:
        """Block-aware incremental emission (R3).

        - New blocks beyond ``_emitted_block_texts``: emit ``\\n\\n``
          separator + full block text.
        - Last block may have grown: emit the text delta.
        - Earlier blocks are stable in append-only streaming: no
          re-emission, no re-join.
        """
        if self._is_clarification:
            # Transitioning from clarification to answer-blocks mode.
            self._is_clarification = False
            self._emitted_block_texts = []
            self._last_block_emitted_len = 0

        deltas: list[str] = []
        prev_block_count = len(self._emitted_block_texts)

        # New blocks appeared beyond what we've already emitted.
        if len(block_texts) > prev_block_count:
            # Seal the current last block (if any) and emit separators
            # + new block texts.
            for i in range(prev_block_count, len(block_texts)):
                if i > 0:
                    deltas.append("\n\n")
                    self._emitted_len += 2  # len("\n\n")
                deltas.append(block_texts[i])
                self._emitted_len += len(block_texts[i])
                self._emitted_block_texts.append(block_texts[i])
            # After appending all new blocks, the last block's emitted
            # length is its full text length.
            self._last_block_emitted_len = len(block_texts[-1])
        elif block_texts:
            # No new blocks, but the last block may have grown.
            last_text = block_texts[-1]
            prev_len = self._last_block_emitted_len
            if len(last_text) > prev_len:
                delta = last_text[prev_len:]
                deltas.append(delta)
                self._emitted_len += len(delta)
                # Update the stored last block text.
                if self._emitted_block_texts:
                    self._emitted_block_texts[-1] = last_text
                self._last_block_emitted_len = len(last_text)

        if not deltas:
            return None
        result = "".join(deltas)
        return result if result else None


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
    # R4-2: generation_id tracks the current model-response generation.
    # Starts at 0 (first generation). Incremented on every tool-result /
    # ModelRetry boundary BEFORE the new generation begins. Each
    # AnswerDeltaEvent carries the current value so the client can
    # attribute deltas to the correct generation and discard stale ones.
    generation_id: int = 0

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
        # R4-2: tag with the current generation_id so the client can
        # discard deltas from a stale generation after a tool-result /
        # ModelRetry boundary reset.
        if not delta:
            return
        event = AnswerDeltaEvent(delta=delta, generation_id=generation_id)
        phase_events.append(event)
        deps.emit_event(event)

    def _emit_preview_reset(
        *,
        reason: (
            Literal["tool_result_boundary"]
            | Literal["tool_argument_model_retry"]
            | Literal["output_validator_model_retry"]
        ),
    ) -> None:
        """R4-2: emit a preview-reset signal at a generation boundary.

        Fired after a tool-result / ModelRetry boundary is detected and
        BEFORE the new model-response stream begins. The new
        ``generation_id`` (post-increment) is included so the client can
        attribute subsequent ``message.delta`` events to the new
        generation. The client MUST clear ``provisional_content_md`` on
        receipt but MUST NOT touch canonical ``content_md``.
        """
        nonlocal generation_id
        generation_id += 1
        event = AnswerPreviewResetEvent(
            generation_id=generation_id,
            reason=reason,
        )
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
        # Context-managed event stream (PydanticAI-recommended API; direct
        # iteration of ``AgentEventStream`` is deprecated). ``__aexit__``
        # guarantees deterministic generator cleanup on normal completion,
        # exception, and cancellation; multi-round tool-call behavior is
        # unchanged (same events, same order, same lifecycle resets).
        # When ToolOutput path rejects via output_validator ModelRetry,
        # pydantic-ai emits OutputToolResultEvent(RetryPromptPart) and then a
        # legacy FunctionToolResultEvent(RetryPromptPart). Skip the legacy
        # twin so generation only advances once (output_validator_retry).
        skip_legacy_function_retry = False

        async with agent.run_stream_events(
            prompt,
            deps=deps,
            model_settings=model_settings,
            usage_limits=usage_limits,
        ) as stream:
            async for event in stream:
                event_kind = getattr(event, "event_kind", None)
                if deps.observation is not None:
                    deps.observation.agent_event_topology.append(
                        type(event).__name__
                    )

                if event_kind == "agent_run_result":
                    run_result = getattr(event, "result", None)
                    if run_result is not None:
                        final_output = getattr(run_result, "output", run_result)
                    continue

                # R4-2 / R4-6: use isinstance type guards (not event_kind
                # string matching) so mypy narrows the union type and the
                # reset boundary is detected robustly across pydantic-ai
                # versions. Each tool-result boundary starts a NEW model
                # response generation — emit a preview_reset so the client
                # clears provisional_content_md before the new generation
                # streams its first delta.

                # Prefer OutputToolResultEvent before FunctionToolResultEvent:
                # output-tool ModelRetry emits both (Output first, then legacy
                # Function twin). Only RetryPromptPart advances generation;
                # successful ToolReturnPart must not advance (end of turn).
                if isinstance(event, OutputToolResultEvent):
                    is_output_retry = isinstance(event.part, RetryPromptPart)
                    if is_output_retry:
                        skip_legacy_function_retry = True
                        _emit_preview_reset(
                            reason="output_validator_model_retry"
                        )
                        lifecycle.reset_stream()
                        answer_streamer.reset()
                        answer_streamed_indices.clear()
                        if thinking_observer is not None:
                            advance_fn = getattr(
                                thinking_observer, "advance_round", None
                            )
                            if callable(advance_fn):
                                advance_fn("output_validator_retry")
                    # Successful output ToolReturnPart: terminal output tool
                    # result — no generation advance, no evidence boundary.
                    continue

                if isinstance(event, FunctionToolResultEvent):
                    # FunctionToolResultEvent carries ToolReturnPart (regular
                    # tool return) or RetryPromptPart (tool-arg ModelRetry).
                    # Both end the current generation and start a new one —
                    # except the legacy twin of an OutputToolResultEvent retry.
                    is_retry = isinstance(event.part, RetryPromptPart)
                    if is_retry and skip_legacy_function_retry:
                        skip_legacy_function_retry = False
                        continue
                    if thinking_observer is not None and not is_retry:
                        # Evidence result only sets evidence_seen — never a
                        # reasoning checkpoint by itself (tool results must
                        # not masquerade as private reasoning).
                        evidence_fn = getattr(
                            thinking_observer, "on_evidence_boundary", None
                        )
                        if callable(evidence_fn):
                            # Prefer part.tool_name — event.result is deprecated
                            # in pydantic-ai and must not be read (DeprecationWarning).
                            tool_name = getattr(event.part, "tool_name", None)
                            if tool_name is None:
                                tool_name = getattr(event, "tool_name", None)
                            evidence_fn(
                                tool_name=tool_name,
                                is_retry=False,
                            )
                    _emit_preview_reset(
                        reason=(
                            "tool_argument_model_retry"
                            if is_retry
                            else "tool_result_boundary"
                        )
                    )
                    lifecycle.reset_stream()
                    answer_streamer.reset()
                    answer_streamed_indices.clear()
                    if thinking_observer is not None:
                        advance_fn = getattr(
                            thinking_observer, "advance_round", None
                        )
                        if callable(advance_fn):
                            advance_fn(
                                "tool_argument_retry"
                                if is_retry
                                else "normal_tool_result"
                            )
                    continue

                if isinstance(event, BuiltinToolResultEvent):
                    # Deprecated event; treat as a regular tool-result
                    # boundary. Newer pydantic-ai versions may not emit
                    # this, but we cover it for safety.
                    if thinking_observer is not None:
                        evidence_fn = getattr(
                            thinking_observer, "on_evidence_boundary", None
                        )
                        if callable(evidence_fn):
                            tool_name = getattr(event, "tool_name", None)
                            evidence_fn(
                                tool_name=tool_name,
                                is_retry=False,
                            )
                    _emit_preview_reset(reason="tool_result_boundary")
                    lifecycle.reset_stream()
                    answer_streamer.reset()
                    answer_streamed_indices.clear()
                    if thinking_observer is not None:
                        advance_fn = getattr(
                            thinking_observer, "advance_round", None
                        )
                        if callable(advance_fn):
                            advance_fn("normal_tool_result")
                    continue

                # R4-6: isinstance type guards for PartStart/Delta/End so
                # mypy narrows the union — no more event_kind string check
                # followed by unsafe union-attribute access.
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
                    # Learner-reasoning CP1/CP2: freeze buffer slice here at
                    # the event source (not in the outer queue consumer).
                    if thinking_observer is not None:
                        segment_end = getattr(
                            thinking_observer,
                            "on_reasoning_segment_end",
                            None,
                        )
                        if callable(segment_end):
                            segment_end()
                    continue

                # R4-A6: answer-block text streaming. TextPart carries
                # streamed structured-output JSON; feed content into the
                # partial parser and emit AnswerDeltaEvent prefix
                # increments. Fully isolated from the ThinkingPart path
                # above: no observer calls, no AnalysisStarted synthesis,
                # no shared content.
                if isinstance(event, PartStartEvent) and isinstance(
                    event.part, TextPart
                ):
                    content = (
                        str(event.part.content) if event.part.content else None
                    )
                    if content:
                        answer_streamed_indices.add(event.index)
                        delta = answer_streamer.feed(content)
                        if delta:
                            _emit_answer_delta(delta)
                            if thinking_observer is not None:
                                first_ans = getattr(
                                    thinking_observer,
                                    "on_first_answer_delta",
                                    None,
                                )
                                if callable(first_ans):
                                    first_ans()
                    continue

                if isinstance(event, PartDeltaEvent) and isinstance(
                    event.delta, TextPartDelta
                ):
                    piece = (
                        str(event.delta.content_delta)
                        if event.delta.content_delta
                        else None
                    )
                    if piece:
                        answer_streamed_indices.add(event.index)
                        delta = answer_streamer.feed(piece)
                        if delta:
                            _emit_answer_delta(delta)
                            if thinking_observer is not None:
                                first_ans = getattr(
                                    thinking_observer,
                                    "on_first_answer_delta",
                                    None,
                                )
                                if callable(first_ans):
                                    first_ans()
                    continue

                if isinstance(event, PartEndEvent) and isinstance(
                    event.part, TextPart
                ):
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
        error = RuntimeError("agent run produced no final output")
        error.reader_ask_raise_site = "transport"  # type: ignore[attr-defined]
        error.reader_ask_final_output_type = "NoneType"  # type: ignore[attr-defined]
        raise error

    if deps.observation is not None:
        deps.observation.transport_final_output_object_id = id(final_output)

    if not isinstance(final_output, AgentAnswerDraftOutput):
        error = TypeError("agent transport received an invalid structured output")
        error.reader_ask_raise_site = "transport"  # type: ignore[attr-defined]
        error.reader_ask_final_output_type = type(final_output).__name__  # type: ignore[attr-defined]
        raise error

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
