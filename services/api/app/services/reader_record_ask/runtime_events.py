"""Typed internal events for the Reading Record Ask agent run.

These are not SSE contracts. Production stream projects a privacy-safe
subset onto ``agentic.progress`` via an optional :class:`RuntimeEventSink`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.reader_record_ask.finalizer import FinalizeStatus

# Optional live observation hook. Production stream may supply a queue-backed
# sink so progress can be projected before the agent run finishes.
RuntimeEventSink = Callable[["RuntimeEvent"], None]


class RunStartedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["run_started"] = "run_started"
    envelope_fingerprint: str
    has_initial_selection: bool


class ToolCallEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_call"] = "tool_call"
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_result"] = "tool_result"
    tool_name: str
    status: str
    summary: str
    evidence_handle_ids: list[str] = Field(default_factory=list)
    payloads: dict[str, Any] | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class FinalAnswerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["final_answer"] = "final_answer"
    text: str


class AnalysisStartedEvent(BaseModel):
    """Safe phase signal: model analysis / thinking has begun.

    Never carries reasoning text, length, hash, or provider payloads.
    Production stream projects a generic agent_running activity only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["analysis_started"] = "analysis_started"


class AnalysisFinishedEvent(BaseModel):
    """Safe phase signal: model analysis / thinking phase completed.

    Never carries reasoning text, length, hash, or provider payloads.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["analysis_finished"] = "analysis_finished"


class ContextCompactionEvent(BaseModel):
    """Safe pre-run memory-compaction lifecycle signal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["context_compaction"] = "context_compaction"
    phase: Literal["started", "completed", "failed", "fallback"]
    detail_code: str | None = Field(default=None, max_length=64)
    attempt_count: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class AnswerDeltaEvent(BaseModel):
    """Safe streaming delta: answer_text prefix increment only (R4-A6).

    Carries user-visible answer text increments — never reasoning text,
    length, hash, or provider payloads. Production stream maps it 1:1 to
    ``message.delta`` SSE and never projects it as agentic progress.

    R4-2: ``generation_id`` is a monotonically increasing counter that
    identifies which model-response generation this delta belongs to.
    The counter starts at 0 for the first generation and increments on
    every tool-result / ModelRetry boundary (see
    :class:`AnswerPreviewResetEvent`). Clients MUST attribute deltas to
    the active generation and discard deltas from a stale generation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["answer_delta"] = "answer_delta"
    delta: str
    generation_id: int = Field(default=0, ge=0)


class AnswerPreviewResetEvent(BaseModel):
    """Safe signal: the answer preview MUST be reset (R4-2).

    Emitted by the thinking transport when a tool result, tool-argument
    ModelRetry, or output-validator ModelRetry boundary is reached and a
    new model-response generation is about to begin. The provisional
    answer text accumulated so far belongs to a now-stale generation and
    MUST be cleared from the client preview.

    ``generation_id`` is the NEW generation counter value (post-increment).
    The first delta of the new generation will carry the same value.
    ``reason`` is a stable machine-readable code (never user content,
    never provider payloads).

    Production stream maps this to ``message.preview_reset`` SSE,
    supplemented with execution_version and turn identity. Never carries
    raw reasoning, provider payloads, or secrets.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["answer_preview_reset"] = "answer_preview_reset"
    generation_id: int = Field(ge=1)
    reason: Literal[
        "tool_result_boundary",
        "tool_argument_model_retry",
        "output_validator_model_retry",
    ]


class ComposingAnswerEvent(BaseModel):
    """Internal signal: agent output received, about to compose/finalize."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["composing_answer"] = "composing_answer"


class ValidatingEvidenceEvent(BaseModel):
    """Grounded-answer-only citation/evidence finalizer lifecycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["validating_evidence"] = "validating_evidence"
    activity: Literal["started", "completed", "failed"]
    outcome: FinalizeStatus | None = None

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> ValidatingEvidenceEvent:
        if self.activity == "started" and self.outcome is not None:
            raise ValueError("started validation must not have an outcome")
        if self.activity == "completed" and self.outcome != "ok":
            raise ValueError("completed validation requires outcome=ok")
        if self.activity == "failed" and self.outcome not in {
            "context_stale",
            "invalid_citations",
            "unavailable",
        }:
            raise ValueError("failed validation requires a failed finalizer outcome")
        return self


class AgenticReasoningStartedEvent(BaseModel):
    """Safe reasoning projection signal: first non-empty projected chunk.

    Emitted by the approved reasoning projector only — never by phase
    events. Carries only identity binding and policy version; never raw
    reasoning, length, hash, or provider payloads. ``seq`` is always 0
    and the event fires at most once per turn.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["agentic_reasoning_started"] = "agentic_reasoning_started"
    execution_version: Literal["reader_record_ask_agentic_v2"] = (
        "reader_record_ask_agentic_v2"
    )
    message_id: str
    thread_id: str
    turn_run_id: str
    seq: int = Field(ge=0)
    projection_policy_version: str


class AgenticReasoningDeltaEvent(BaseModel):
    """Safe reasoning projection increment.

    ``delta`` is already projected (deterministic redaction + quota) by
    the server-side chokepoint; clients append, never filter. Raw
    reasoning, length, hash, or provider payloads never appear here.
    ``seq`` is strictly monotonic (1..n); only non-empty projected
    increments consume a seq.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["agentic_reasoning_delta"] = "agentic_reasoning_delta"
    execution_version: Literal["reader_record_ask_agentic_v2"] = (
        "reader_record_ask_agentic_v2"
    )
    message_id: str
    thread_id: str
    turn_run_id: str
    seq: int = Field(ge=1)
    delta: str = Field(min_length=1)


class AgenticReasoningCompletedEvent(BaseModel):
    """Safe reasoning projection completion promise.

    Built by the projector host ONLY after the projection and the final
    answer were persisted in the same successful transaction; production
    stream emits it before ``message.completed``. Never emitted on
    cancel / validation-failure / budget-exhausted / persist-failure
    paths — those never persist reasoning and never complete it.
    Carries only booleans, seq, and policy version; never content.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["agentic_reasoning_completed"] = "agentic_reasoning_completed"
    execution_version: Literal["reader_record_ask_agentic_v2"] = (
        "reader_record_ask_agentic_v2"
    )
    message_id: str
    thread_id: str
    turn_run_id: str
    seq: int = Field(ge=1)
    has_content: bool
    truncated: bool
    projection_policy_version: str


class RunFinishedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["run_finished"] = "run_finished"
    read_range_calls: int
    evidence_count: int
    search_current_article_calls: int = 0
    # G1-b5: web search call count (host-owned; never model-supplied).
    # Mirrors ``search_current_article_calls`` semantics so observers
    # can audit the per-turn web search budget. ``0`` when the
    # capability was not enabled or no call was made.
    web_search_calls: int = 0


# ---------------------------------------------------------------------------
# Web search typed events (G1)
# ---------------------------------------------------------------------------
#
# These mirror ``ToolCallEvent`` / ``ToolResultEvent`` but carry a
# typed ``tool_name="search_web"`` discriminator plus web-specific
# status / outcome fields. They never carry query text, snippets,
# provider payloads, or reasoning — privacy-safe projection only.
# Production stream projects ``searching_web`` progress phase from
# ``WebSearchCallEvent`` and ``ok``/``unavailable``/``failed`` activity
# from ``WebSearchResultEvent``.


class WebSearchCallEvent(BaseModel):
    """Internal signal: agent invoked ``search_web`` (G1).

    Carries only identity-free metadata. ``query`` is intentionally
    absent — the model-supplied query text is untrusted content and
    must never appear in observability events. Production stream
    projects a generic ``searching_web`` progress phase only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["web_search_call"] = "web_search_call"
    # Sequence of the call within the turn (1-based). Lets observers
    # order multiple search calls without exposing the query text.
    call_sequence: int = Field(ge=1)
    # ``None`` for the live "started" state: the Host has not yet proved a
    # provider invocation occurred. The paired result event carries the
    # authoritative, monotonically non-decreasing real invocation count.
    attempt_count: int | None = Field(default=None, ge=0)


class WebSearchResultEvent(BaseModel):
    """Internal signal: ``search_web`` returned (G1).

    Carries only the typed outcome and the count of registered
    :class:`WebEvidence` entries (opaque handles). Never carries the
    raw provider result count, scores, URLs, titles, descriptions, or
    any provider payload. Production stream projects
    ``ok`` / ``unavailable`` / ``failed`` / ``timeout`` activity only.

    ASK-WEB-R4: attempt vs turn-level outcome separation.

    - ``outcome`` is the **per-attempt** outcome (this single call's
      result). Used for telemetry only.
    - ``turn_outcome`` is the **turn-level aggregated** outcome at the
      time of this attempt (strongest-wins: completed > timeout >
      no_results > unavailable > failed). Used by the production-stream
      projector for UI activity so a ``call_limit`` attempt after a
      successful search does NOT degrade the turn-level status to
      ``unavailable``.
    - ``detail_code`` is a safe per-attempt reason code (e.g.
      ``"call_limit"``, ``"ok"``, ``"empty"``, ``"fence_pre"``).
      Never carries query / URL / provider payload.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["web_search_result"] = "web_search_result"
    call_sequence: int = Field(ge=1)
    # Actual provider invocation count at this point. It can differ from the
    # tool call sequence when the host rejects a normalization-equivalent
    # reformulation without contacting a provider.
    attempt_count: int = Field(default=0, ge=0)
    # Per-attempt outcome (this single call only).
    outcome: Literal[
        "completed", "no_results", "unavailable", "failed", "timeout"
    ]
    # Turn-level aggregated outcome at the time of this attempt.
    # The projector uses this for UI activity so call_limit after
    # success does not degrade to ``unavailable``.
    turn_outcome: Literal[
        "completed", "no_results", "unavailable", "failed", "timeout"
    ] = Field(default="unavailable")
    # Per-attempt safe detail code (never query / URL / payload).
    detail_code: str | None = Field(default=None, max_length=64)
    # Count of host-minted :class:`WebEvidence` entries from this call.
    # Always 0 for ``no_results`` / ``unavailable`` / ``failed`` / ``timeout``.
    registered_evidence_count: int = Field(default=0, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)


RuntimeEvent = (
    RunStartedEvent
    | ToolCallEvent
    | ToolResultEvent
    | AnalysisStartedEvent
    | AnalysisFinishedEvent
    | ContextCompactionEvent
    | AnswerDeltaEvent
    | AnswerPreviewResetEvent
    | ComposingAnswerEvent
    | ValidatingEvidenceEvent
    | FinalAnswerEvent
    | RunFinishedEvent
    | AgenticReasoningStartedEvent
    | AgenticReasoningDeltaEvent
    | AgenticReasoningCompletedEvent
    | WebSearchCallEvent
    | WebSearchResultEvent
)
