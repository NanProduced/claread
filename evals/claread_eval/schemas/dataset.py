from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, Field

ReadingGoal = Literal["exam", "daily_reading", "academic"]
ReadingVariant = Literal[
    "gaokao",
    "cet",
    "kaoyan",
    "tem",
    "ielts_toefl",
    "beginner_reading",
    "intermediate_reading",
    "intensive_reading",
    "academic_general",
]
SourceType = Literal["user_input", "daily_article", "imported", "ocr"]
CaseOrigin = Literal["dataset", "adhoc", "generated"]

GOAL_VARIANT_MAP: dict[ReadingGoal, set[ReadingVariant]] = {
    "exam": {"gaokao", "cet", "kaoyan", "tem", "ielts_toefl"},
    "daily_reading": {"beginner_reading", "intermediate_reading", "intensive_reading"},
    "academic": {"academic_general"},
}


class EvalCaseExpected(BaseModel):
    min_translation_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    allowed_warning_codes: list[str] = Field(default_factory=list)
    tolerated_warning_codes: list[str] = Field(
        default_factory=lambda: ["LOW_ENGLISH_RATIO", "TEXT_TYPE_NEEDS_CARE"],
    )
    max_warning_count: int | None = Field(default=None, ge=0)
    max_drop_ratio: float | None = Field(default=None, ge=0.0, le=1.0)


class EvalCase(BaseModel):
    id: str = Field(description="Case unique identifier within dataset")
    origin: CaseOrigin = Field(default="dataset")
    text: str = Field(min_length=1, description="Source article text")
    reading_goal: ReadingGoal = Field(description="Reading goal category")
    reading_variant: ReadingVariant = Field(description="Reading variant subcategory")
    source_type: SourceType = Field(default="user_input")
    tags: list[str] = Field(default_factory=list)
    difficulty: str | None = Field(default=None)
    target_phenomena: list[str] = Field(default_factory=list)
    expected: EvalCaseExpected = Field(default_factory=EvalCaseExpected)
    reference_notes: str | None = Field(default=None)
    extended: bool = Field(default=False)

    def model_post_init(self, __context__: object) -> None:
        allowed = GOAL_VARIANT_MAP.get(self.reading_goal)
        if allowed and self.reading_variant not in allowed:
            raise ValueError(
                f"reading_variant={self.reading_variant} not valid for "
                f"reading_goal={self.reading_goal}"
            )


class AdHocEvalCaseInput(BaseModel):
    text: str = Field(min_length=1, description="Manual source article text")
    reading_goal: ReadingGoal = Field(default="daily_reading")
    reading_variant: ReadingVariant = Field(default="intermediate_reading")
    source_type: SourceType = Field(default="user_input")
    extended: bool = Field(default=False)
    tags: list[str] = Field(default_factory=lambda: ["adhoc"])
    reference_notes: str | None = Field(default=None)
    expected: EvalCaseExpected = Field(default_factory=EvalCaseExpected)

    def model_post_init(self, __context__: object) -> None:
        allowed = GOAL_VARIANT_MAP.get(self.reading_goal)
        if allowed and self.reading_variant not in allowed:
            raise ValueError(
                f"reading_variant={self.reading_variant} not valid for "
                f"reading_goal={self.reading_goal}"
            )

    def to_eval_case(self, *, case_id: str | None = None) -> EvalCase:
        stable_hash = sha256(
            "|".join(
                [
                    self.text.strip(),
                    self.reading_goal,
                    self.reading_variant,
                    str(self.extended),
                ]
            ).encode("utf-8")
        ).hexdigest()[:12]
        return EvalCase(
            id=case_id or f"adhoc-{stable_hash}",
            origin="adhoc",
            text=self.text,
            reading_goal=self.reading_goal,
            reading_variant=self.reading_variant,
            source_type=self.source_type,
            tags=self.tags,
            expected=self.expected,
            reference_notes=self.reference_notes,
            extended=self.extended,
        )


class EvalDataset(BaseModel):
    id: str = Field(description="Dataset unique identifier")
    schema_version: str = Field(default="eval-dataset-v1")
    target: str = Field(description="Eval target, e.g. article_analysis")
    description: str = Field(default="")
    case_globs: list[str] = Field(default_factory=lambda: ["cases/*.json"])
    tags: list[str] = Field(default_factory=list)
