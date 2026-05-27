from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

OverviewHintStatus = Literal["pending", "ready", "unavailable", "failed", "stale"]
OverviewHintReadyStatus = Literal["ready", "unavailable"]
OverviewHintConfidence = Literal["high", "medium", "low"]
OverviewHintSource = Literal["learning_overview_hint_agent", "academic_render_scene"]


class LearningOverviewHintDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    status: OverviewHintReadyStatus
    overview: str | None = Field(
        default=None,
        description="简短 overview hint，仅在 status=ready 时提供。",
    )
    confidence: OverviewHintConfidence | None = Field(
        default=None,
        description="仅在 status=ready 时按需提供。",
    )
    reason: str | None = Field(
        default=None,
        description="仅在 status=unavailable 时提供简短原因。",
    )

    @model_validator(mode="after")
    def validate_shape(self) -> "LearningOverviewHintDraft":
        if self.status == "ready" and not (self.overview or "").strip():
            raise ValueError("ready overview hints require overview")
        if self.status == "unavailable" and not (self.reason or "").strip():
            raise ValueError("unavailable overview hints require reason")
        return self


class StoredOverviewHint(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    status: OverviewHintStatus
    overview: str | None = None
    confidence: OverviewHintConfidence | None = None
    reason: str | None = None
    source: OverviewHintSource | None = None
    source_text_hash: str | None = None
    workflow_version: str | None = None
    schema_version: str | None = None
    updated_at: str | None = None
    task_id: str | None = None
