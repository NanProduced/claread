from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class RubricLoadError(ValueError):
    pass


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    score_min: int = Field(default=1)
    score_max: int = Field(default=5)
    pass_score: int = Field(default=4)
    weight: float = Field(default=1.0, gt=0.0)
    evidence_fields: list[str] = Field(default_factory=list)


class RubricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    target: Literal["article_analysis"]
    description: str = ""
    criteria: list[RubricCriterion]


class RubricCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric_id: str
    rubric_version: str
    case_id: str
    run_id: str
    prompt_identity: dict
    model_identity: dict
    reading_goal: str | None = None
    reading_variant: str | None = None
    source_text_excerpt: str
    output_excerpt: dict
    criteria: list[RubricCriterion]


def load_rubric(path: str | Path) -> RubricSpec:
    rubric_path = Path(path)
    if not rubric_path.is_file():
        raise RubricLoadError(f"Rubric file not found: {rubric_path}")
    raw = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RubricLoadError(f"Rubric file must contain an object: {rubric_path}")
    return RubricSpec.model_validate(raw)
