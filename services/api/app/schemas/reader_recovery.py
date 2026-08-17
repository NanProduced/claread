"""Reader manual recovery response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.reader_orchestration import ReadingRecordProductState

ReaderRecoveryOutcome = Literal["recovery_started", "nothing_to_recover"]


class ReaderRecoveryResponse(BaseModel):
    """Minimal client-facing result of one manual recovery attempt.

    Covers both a real recovery and the idempotent no-op with HTTP 200;
    internal identifiers (base/job/run, event flags) stay out of it.
    """

    model_config = ConfigDict(extra="forbid")

    record_id: str
    outcome: ReaderRecoveryOutcome
    previous_product_state: ReadingRecordProductState
    next_product_state: ReadingRecordProductState
    record_generation: int = Field(ge=1)
    successor_job_count: int = Field(ge=0)
