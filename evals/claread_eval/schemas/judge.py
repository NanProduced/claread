from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JudgeCaseStatus = Literal["succeeded", "error"]
JudgeVerdict = Literal["pass", "fail", "needs_review", "error"]


class JudgeCriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    label: str | None = None
    score: float | None = None
    passed: bool | None = None
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)


class JudgeCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    status: JudgeCaseStatus = "succeeded"
    verdict: JudgeVerdict = "needs_review"
    overall_score: float | None = None
    pass_threshold: float | None = None
    criteria: list[JudgeCriterionResult] = Field(default_factory=list)
    summary: str = ""
    error: dict[str, Any] | None = None
    judge_adapter_kind: str | None = None


class JudgeRunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "eval-judge-run-v1"
    judge_run_id: str
    run_id: str
    rubric_id: str
    rubric_version: str
    judge_adapter_kind: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    config_json: dict[str, Any] = Field(default_factory=dict)


class JudgeCaseSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: JudgeCaseStatus
    verdict: JudgeVerdict
    overall_score: float | None = None
    error: dict[str, Any] | None = None


class JudgeRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "eval-judge-report-v1"
    judge_run_id: str
    run_id: str
    rubric_id: str
    rubric_version: str
    judge_adapter_kind: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    needs_review: int = 0
    errored: int = 0
    average_score: float | None = None
    low_score_case_ids: list[str] = Field(default_factory=list)
    case_summaries: list[JudgeCaseSummary] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
