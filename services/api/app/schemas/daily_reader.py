"""Daily Reader API schemas."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class DailyReaderArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    subtitle: str | None = None
    source: str
    source_url: str
    publish_date: date
    difficulty: str
    read_time_minutes: int
    tags: list[str] = Field(default_factory=list)
    cover_image_url: str | None = None
    cover_theme: str = "editorial_warm"
    body: dict = Field(default_factory=dict)
    highlights: list[dict] = Field(default_factory=list)
    paragraph_notes: dict = Field(default_factory=dict)
    takeaways: dict = Field(default_factory=dict)


class DailyReaderTodayResponse(BaseModel):
    articles: list[DailyReaderArticleResponse]


class DailyReaderListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    subtitle: str | None = None
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


class DailyReaderGenerateRequest(BaseModel):
    force: bool = False
    source_preference: str | None = None
    max_count: int = Field(default=3, ge=1, le=5)


class DailyReaderGenerateResponse(BaseModel):
    task_id: str
    status: str
    message: str


class DailyReaderPublishRequest(BaseModel):
    id: str


class DailyReaderUnpublishRequest(BaseModel):
    id: str


class DailyReaderRetryRequest(BaseModel):
    id: str


class ArticleActionResponse(BaseModel):
    status: str


class RetryWorkflowResponse(BaseModel):
    status: str
    message: str
