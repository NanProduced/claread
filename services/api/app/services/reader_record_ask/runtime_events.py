"""Typed internal events for the Reading Record Ask agent run.

These are not SSE contracts. Production stream projects a privacy-safe
subset onto ``agentic.progress`` via an optional :class:`RuntimeEventSink`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class AnswerDeltaEvent(BaseModel):
    """Safe streaming delta: answer_text prefix increment only (R4-A6).

    Carries user-visible answer text increments — never reasoning text,
    length, hash, or provider payloads. Production stream maps it 1:1 to
    ``message.delta`` SSE and never projects it as agentic progress.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["answer_delta"] = "answer_delta"
    delta: str


class ComposingAnswerEvent(BaseModel):
    """Internal signal: agent output received, about to compose/finalize."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["composing_answer"] = "composing_answer"


class ValidatingEvidenceEvent(BaseModel):
    """Internal signal: finalizer is about to validate citations/evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["validating_evidence"] = "validating_evidence"


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


RuntimeEvent = (
    RunStartedEvent
    | ToolCallEvent
    | ToolResultEvent
    | AnalysisStartedEvent
    | AnalysisFinishedEvent
    | AnswerDeltaEvent
    | ComposingAnswerEvent
    | ValidatingEvidenceEvent
    | FinalAnswerEvent
    | RunFinishedEvent
    | AgenticReasoningStartedEvent
    | AgenticReasoningDeltaEvent
    | AgenticReasoningCompletedEvent
)
