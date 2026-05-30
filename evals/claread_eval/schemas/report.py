from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from claread_eval.schemas.grader import GraderVerdict


class CaseSummary(BaseModel):
    case_id: str
    verdict: GraderVerdict = GraderVerdict.PASS
    hard_failures: int = 0
    soft_failures: int = 0
    error: str | None = None


class EvalReport(BaseModel):
    run_id: str
    dataset_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errored: int = 0
    hard_failure_case_ids: list[str] = Field(default_factory=list)
    soft_failure_case_ids: list[str] = Field(default_factory=list)
    case_summaries: list[CaseSummary] = Field(default_factory=list)
    runtime_aggregates: dict[str, Any] = Field(default_factory=dict)
    regression_list: list[str] = Field(default_factory=list)
