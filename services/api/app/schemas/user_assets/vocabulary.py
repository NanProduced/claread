"""
User Assets API Schemas: Vocabulary Book.

Defines request/response Pydantic models for /vocabulary endpoints.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared Sub-models
# ---------------------------------------------------------------------------


class SourceRef(BaseModel):
    """单次收藏来源的语境记录，存储在 payload_json.source_refs 中。"""

    client_record_id: str = Field(default="", max_length=128)
    cloud_record_id: str | None = Field(default=None, max_length=128)
    source_sentence: str | None = Field(default=None)
    source_context: str | None = Field(default=None)
    source_sentence_id: str | None = Field(default=None, max_length=64)
    source_anchor_text: str | None = Field(default=None, max_length=256)
    source_occurrence: int | None = Field(default=None, ge=1)
    collected_at: str | None = Field(default=None)


class ReviewPayload(BaseModel):
    """复习调度数据，嵌入 payload_json.review。"""

    stage: int = Field(default=0, ge=0, description="当前复习阶段 (0-5)")
    next_review_at: str | None = Field(
        default=None,
        description="下次复习时间 ISO-8601 字符串",
    )
    last_result: str | None = Field(
        default=None,
        description="上次复习结果: known / unfamiliar",
    )
    last_reviewed_at: str | None = Field(
        default=None,
        description="上次复习时间 ISO-8601 字符串",
    )


class VocabularyPayload(BaseModel):
    """payload_json 的结构化模型，便于类型安全地读写扩展元数据。"""

    source_refs: list[SourceRef] = Field(default_factory=list)
    collected_forms: list[str] = Field(default_factory=list)
    audio_url: str | None = Field(default=None, max_length=512)
    review: ReviewPayload | None = Field(default=None)

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class VocabularyCreateRequest(BaseModel):
    """POST /vocabulary — add a word/phrase to vocabulary book."""

    lemma: str = Field(min_length=1, max_length=256)
    display_word: str = Field(min_length=1, max_length=256)
    phonetic: str | None = Field(default=None, max_length=256)
    part_of_speech: str | None = Field(default=None, max_length=64)
    short_meaning: str = Field(min_length=1)
    meanings_json: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    exchange: list[str] = Field(default_factory=list)
    source_provider: str = Field(default="tecd3")
    dict_entry_id: int | None = Field(default=None, description="词典词条稳定引用 ID")
    source_sentence: str | None = Field(default=None)
    source_context: str | None = Field(default=None)
    payload_json: dict[str, Any] = Field(default_factory=dict)


class VocabularyUpdateRequest(BaseModel):
    """PATCH /vocabulary/{id} — update a vocabulary entry."""

    mastery_status: str | None = Field(default=None)
    short_meaning: str | None = Field(default=None)
    payload_json: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------


class VocabularyResponse(BaseModel):
    """Single vocabulary entry."""

    id: UUID
    user_id: UUID
    lemma: str
    display_word: str
    phonetic: str | None
    part_of_speech: str | None
    short_meaning: str
    meanings_json: list[dict[str, Any]] | None = Field(default=None)
    tags: list[str]
    exchange: list[str]
    source_provider: str
    dict_entry_id: int | None = Field(default=None, description="词典词条稳定引用 ID")
    source_sentence: str | None = Field(default=None)
    source_context: str | None = Field(default=None)
    mastery_status: str
    review_count: int
    last_reviewed_at: datetime | None
    payload_json: dict[str, Any] | None = Field(default=None)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VocabularyListResponse(BaseModel):
    """GET /vocabulary — paginated list."""

    items: list[VocabularyResponse]
    total: int
    page: int
    limit: int


class VocabularyUpsertResponse(BaseModel):
    """POST /vocabulary — upsert result."""

    id: UUID
    lemma: str
    created: bool
    updated_at: datetime


class VocabularyDeleteResponse(BaseModel):
    """DELETE /vocabulary/{vocab_id} — result."""

    deleted: bool


# ---------------------------------------------------------------------------
# Review Models
# ---------------------------------------------------------------------------


class ReviewResultEnum(str, Enum):
    """复习动作枚举。"""

    known = "known"
    unfamiliar = "unfamiliar"


class ReviewSubmitRequest(BaseModel):
    """POST /vocabulary/{vocab_id}/review — 提交复习结果。"""

    result: ReviewResultEnum


class ReviewResultResponse(BaseModel):
    """POST /vocabulary/{vocab_id}/review — 返回更新后的复习状态。"""

    vocab_id: UUID
    lemma: str
    stage: int
    next_review_at: str | None
    mastery_status: str
    review_count: int


# ---------------------------------------------------------------------------
# Highlights (Result Page Overlay)
# ---------------------------------------------------------------------------


class SentenceTokens(BaseModel):
    """单个句子的 token 列表，用于 highlights 请求。"""

    sentence_id: str = Field(min_length=1, max_length=64)
    tokens: list[str] = Field(min_length=1)


class VocabHighlightsRequest(BaseModel):
    """POST /vocabulary/highlights — 查询句子中已收藏的生词匹配。"""

    sentences: list[SentenceTokens] = Field(min_length=1, max_length=200)


class VocabMatchItem(BaseModel):
    """单个匹配结果：句子中某个 token 匹配到了用户生词本中的词条。"""

    vocab_id: UUID
    lemma: str
    sentence_id: str
    anchor_text: str
    occurrence: int
    mastery_status: str


class VocabHighlightsResponse(BaseModel):
    """POST /vocabulary/highlights — 匹配结果。"""

    matches: list[VocabMatchItem]
