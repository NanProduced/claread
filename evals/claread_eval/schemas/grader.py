from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GraderVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


class GraderSeverity(str, Enum):
    HARD = "hard"
    SOFT = "soft"
    INFO = "info"


class GraderResult(BaseModel):
    grader_name: str = Field(description="Identifier of the grader that produced this result")
    case_id: str = Field(description="EvalCase id this result applies to")
    verdict: GraderVerdict = Field(description="pass / fail / skip / error")
    severity: GraderSeverity = Field(default=GraderSeverity.HARD)
    metric: str = Field(default="", description="What was measured")
    value: Any = Field(default=None, description="Measured value")
    expected: Any = Field(default=None, description="Expected value or threshold")
    evidence: str = Field(default="", description="Human-readable explanation")
