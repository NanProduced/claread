from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.analysis import AnalyzeRequestMeta, ContentSummary, SourceType


class ReaderRecordMeta(BaseModel):
    id: UUID
    client_record_id: str | None = None
    title: str | None = None
    source_type: SourceType
    source_text: str
    request_payload_json: dict[str, Any] = Field(default_factory=dict)
    reading_goal: str | None = None
    reading_variant: str | None = None
    analysis_status: str
    user_facing_state: str | None = None
    workflow_version: str | None = None
    schema_version: str | None = None
    created_at: datetime
    updated_at: datetime


class ReaderArticleParagraph(BaseModel):
    paragraph_id: str
    sentence_ids: list[str] = Field(default_factory=list)


class ReaderArticleSentence(BaseModel):
    sentence_id: str
    paragraph_id: str
    text: str


class ReaderArticle(BaseModel):
    paragraphs: list[ReaderArticleParagraph] = Field(default_factory=list)
    sentences: list[ReaderArticleSentence] = Field(default_factory=list)
    source_text: str | None = None


class ReaderSceneModel(BaseModel):
    schema_version: str
    request: AnalyzeRequestMeta
    article: ReaderArticle
    user_facing_state: str = "normal"
    translations: list[dict[str, Any]] = Field(default_factory=list)
    inline_marks: list[dict[str, Any]] = Field(default_factory=list)
    sentence_entries: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    content_summary: ContentSummary | None = None
    title: str | None = None


class ReaderViewMeta(BaseModel):
    view_version: str
    data_source: Literal["render_scene_snapshot", "source_text_fallback"]
    fallback_mode: Literal["none", "article_rebuilt_from_source_text", "scene_missing"]
    supplements_merged: bool


class ReaderSceneResponse(BaseModel):
    record_meta: ReaderRecordMeta
    reader_scene: ReaderSceneModel
    view_meta: ReaderViewMeta
