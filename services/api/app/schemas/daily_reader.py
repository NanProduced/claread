"""Daily Reader API schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DailyReaderArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    subtitle: str | None = None
    # A-3: English source headline (caption-level display) and Chinese
    # subtitle; None on rows produced before A-3.
    original_title: str | None = None
    subtitle_zh: str | None = None
    source: str
    source_url: str
    publish_date: date
    difficulty: str
    read_time_minutes: int
    tags: list[str] = Field(default_factory=list)
    cover_image_url: str | None = None
    cover_theme: str = "editorial_warm"
    # P-5B teaching-v2 payload (zero projection: the v1 body/highlights/
    # paragraph_notes/takeaways fields are gone). lesson_blueprint and
    # learning_package come from the lesson_v2 column; reading_units is the
    # plain article body (body_json paragraphs, unit ids match the lesson
    # anchors). NULL lesson_v2 = pre-v2 row: payload fields stay empty.
    lesson_blueprint: dict = Field(default_factory=dict)
    learning_package: dict = Field(default_factory=dict)
    reading_units: list[dict] = Field(default_factory=list)


class DailyReaderTodayResponse(BaseModel):
    articles: list[DailyReaderArticleResponse]


class DailyReaderListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    subtitle: str | None = None
    # A-3: see DailyReaderArticleResponse.
    original_title: str | None = None
    subtitle_zh: str | None = None
    source: str
    publish_date: date
    difficulty: str
    read_time_minutes: int
    tags: list[str] = Field(default_factory=list)
    cover_image_url: str | None = None
    cover_theme: str = "editorial_warm"


class DailyReaderListResponse(BaseModel):
    items: list[DailyReaderListItem]
    cursor: str | None = None
    has_more: bool = False


class DailyReaderCoverCandidate(BaseModel):
    url: str
    upgraded_url: str | None = None
    position: str | None = None
    valid: bool = False
    reason: str | None = None
    width: int | None = None
    height: int | None = None


class DailyReaderSelectedCover(BaseModel):
    url: str
    source_url: str | None = None
    width: int | None = None
    height: int | None = None
    caption_zh: str | None = None


class DailyReaderReviewMachineFlags(BaseModel):
    cover_missing: bool
    cover_quality: Literal["qualified", "missing", "unavailable"]
    cover_width: int | None = None
    cover_height: int | None = None
    boilerplate_suspected: bool
    boilerplate_hits: list[str] = Field(default_factory=list)


class DailyReaderReviewQueueItem(BaseModel):
    id: str
    title: str
    subtitle: str | None = None
    original_title: str | None = None
    subtitle_zh: str | None = None
    source: str
    source_url: str
    publish_date: date
    difficulty: str
    read_time_minutes: int
    tags: list[str] = Field(default_factory=list)
    cover_image_url: str | None = None
    cover_theme: str
    selection_score: float | None = None
    review_score: float | None = None
    review_score_available: bool = False
    status: str
    review_status: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    machine_flags: DailyReaderReviewMachineFlags
    cover_candidates: list[DailyReaderCoverCandidate] = Field(default_factory=list)
    selected_cover: DailyReaderSelectedCover | None = None


class DailyReaderReviewQueueResponse(BaseModel):
    items: list[DailyReaderReviewQueueItem]
    limit: int
    offset: int
    has_more: bool


class DailyReaderAdminUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=300)
    subtitle_zh: str | None = Field(default=None, max_length=500)
    cover_image_url: str | None = Field(default=None, max_length=2048)
    tags: list[str] | None = Field(default=None, max_length=8)

    @field_validator("title")
    @classmethod
    def title_cannot_be_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("title cannot be null")
        return value

    @field_validator("cover_image_url")
    @classmethod
    def validate_cover_url(cls, value: str | None) -> str | None:
        if value is None or (value.startswith("/") and not value.startswith("//")):
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("cover_image_url must be an http(s) or root-relative URL")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str]:
        if value is None:
            raise ValueError("tags cannot be null")
        normalized: list[str] = []
        for tag in value:
            cleaned = tag.strip()
            if not cleaned:
                raise ValueError("tags cannot contain blank values")
            if len(cleaned) > 30:
                raise ValueError("tag must be at most 30 characters")
            if cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> DailyReaderAdminUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("body must include at least one editable field")
        return self


class DailyReaderAdminUpdateResponse(BaseModel):
    id: str
    status: Literal["updated", "unchanged"]
    review_status: Literal["pending"]


class DailyReaderGenerateRequest(BaseModel):
    force: bool = False
    source_preference: str | None = None
    single: bool = Field(
        default=False,
        description="Console shortcut: generate at most one article (overrides max_count)",
    )
    max_count: int = Field(default=3, ge=1, le=5)


class DailyReaderGenerateResponse(BaseModel):
    task_id: str
    status: str
    message: str


class DailyReaderPublishRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str
    operator: str = Field(min_length=1)


class DailyReaderUnpublishRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str
    operator: str = Field(min_length=1)


class DailyReaderRetryRequest(BaseModel):
    id: str


class ArticleActionResponse(BaseModel):
    status: str


class RetryWorkflowResponse(BaseModel):
    status: str
    message: str
