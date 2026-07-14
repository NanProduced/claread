"""Typed internal events for the Reading Record Ask agent run.

These are not SSE contracts.  A later slice may map them to the stream.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class FinalAnswerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["final_answer"] = "final_answer"
    text: str


class RunFinishedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["run_finished"] = "run_finished"
    read_range_calls: int
    evidence_count: int


RuntimeEvent = (
    RunStartedEvent
    | ToolCallEvent
    | ToolResultEvent
    | FinalAnswerEvent
    | RunFinishedEvent
)
