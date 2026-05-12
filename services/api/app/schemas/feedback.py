"""
Feedback System API Schemas.

Defines request/response Pydantic models for /feedback endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

FEEDBACK_TYPES_BY_SCOPE: dict[str, dict[str, list[str]]] = {
    "analysis_result": {
        "positive": ["thumbs_up"],
        "negative": [
            "translation_inaccurate",
            "too_few_annotations",
            "too_many_annotations",
            "wrong_difficulty",
            "other",
        ],
    },
    "annotation": {
        "positive": ["helpful"],
        "negative": [
            "wrong_label",
            "inaccurate",
            "wrong_boundary",
            "should_not_annotate",
            "other",
        ],
    },
    "sentence": {
        "negative": [
            "translation_inaccurate",
            "sentence_analysis_wrong",
            "annotation_conflict",
            "selection_issue",
            "other",
        ],
    },
    "dictionary": {
        "negative": [
            "wrong_definition",
            "missing_definition",
            "wrong_pos",
            "wrong_phonetic",
            "bad_example",
            "other",
        ],
    },
    "app": {
        "neutral": [
            "bug_report",
            "feature_request",
            "quota_issue",
            "input_page_issue",
            "ux_issue",
            "other",
        ],
    },
}

ALL_FEEDBACK_TYPES: set[str] = set()
for _group in FEEDBACK_TYPES_BY_SCOPE.values():
    for _types in _group.values():
        ALL_FEEDBACK_TYPES.update(_types)

FeedbackScope = Literal["analysis_result", "annotation", "sentence", "dictionary", "app"]
Sentiment = Literal["positive", "negative", "neutral"]


class FeedbackCreateRequest(BaseModel):
    """POST /feedback — submit feedback."""

    feedback_scope: FeedbackScope
    target_id: str = Field(min_length=1, max_length=256)
    analysis_record_id: UUID | None = Field(default=None)
    sentiment: Sentiment
    feedback_type: str = Field(min_length=1, max_length=64)
    annotation_type: str | None = Field(default=None, max_length=64)
    content: str | None = Field(default=None, max_length=2000)
    context_json: dict[str, Any] = Field(default_factory=dict)
    app_version: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_scope_rules(self) -> "FeedbackCreateRequest":
        scope = self.feedback_scope
        ft = self.feedback_type
        sent = self.sentiment

        scope_types = FEEDBACK_TYPES_BY_SCOPE.get(scope)
        if scope_types is None:
            raise ValueError(f"Unknown feedback_scope: {scope}")

        valid_types: set[str] = set()
        for group in scope_types.values():
            valid_types.update(group)
        if ft not in valid_types:
            raise ValueError(
                f"feedback_type '{ft}' is not valid for scope '{scope}'. "
                f"Valid types: {sorted(valid_types)}"
            )

        expected_sentiment: str | None = None
        for sentiment_key, types in scope_types.items():
            if ft in types:
                expected_sentiment = sentiment_key
                break
        if expected_sentiment and sent != expected_sentiment:
            raise ValueError(
                f"feedback_type '{ft}' requires sentiment '{expected_sentiment}', "
                f"got '{sent}'"
            )

        if scope == "dictionary" and sent != "negative":
            raise ValueError("dictionary feedback only allows negative sentiment")

        if scope == "annotation" and not self.annotation_type:
            raise ValueError("annotation_type is required for annotation scope")

        if scope in ("analysis_result", "annotation") and not self.analysis_record_id:
            raise ValueError(
                f"analysis_record_id is required for {scope} scope"
            )

        return self


class FeedbackResponse(BaseModel):
    """POST /feedback — submit response."""

    id: UUID
    feedback_scope: str
    target_id: str
    sentiment: str
    feedback_type: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FeedbackListItem(BaseModel):
    """GET /feedback — list item."""

    id: UUID
    feedback_scope: str
    feedback_type: str
    sentiment: str
    content: str | None
    status: str
    reward_points: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FeedbackListResponse(BaseModel):
    """GET /feedback — paginated list."""

    items: list[FeedbackListItem]
    cursor: str | None
    has_more: bool


class FeedbackStatusUpdateRequest(BaseModel):
    """PATCH /internal/feedback/{id}/status — update status."""

    status: Literal["adopted", "resolved", "dismissed"]
    admin_note: str | None = Field(default=None, max_length=2000)


class FeedbackRewardRequest(BaseModel):
    """POST /internal/feedback/{id}/reward — grant reward points."""

    points: int = Field(gt=0, le=500)


class FeedbackRewardResponse(BaseModel):
    """POST /internal/feedback/{id}/reward — reward result."""

    feedback_id: str
    user_id: str
    reward_points: int
    granted: bool


class FeedbackStatusUpdateResponse(BaseModel):
    """PATCH /internal/feedback/{id}/status — update result."""

    id: str
    status: str
    admin_note: str | None = None


class FeedbackStatsResponse(BaseModel):
    """GET /internal/feedback/stats — statistics."""

    total: int
    pending: int
    adopted: int
    resolved: int
    dismissed: int
