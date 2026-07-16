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


class ComposingAnswerEvent(BaseModel):
    """Internal signal: agent output received, about to compose/finalize."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["composing_answer"] = "composing_answer"


class ValidatingEvidenceEvent(BaseModel):
    """Internal signal: finalizer is about to validate citations/evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["validating_evidence"] = "validating_evidence"


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
    | ComposingAnswerEvent
    | ValidatingEvidenceEvent
    | FinalAnswerEvent
    | RunFinishedEvent
)
