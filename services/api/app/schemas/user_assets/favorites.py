"""
User Assets API Schemas: Favorites.

Defines request/response Pydantic models for /favorites endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic import model_validator

from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length

FavoriteTargetType = Literal[
    "analysis_record",
    "sentence",
    "paragraph",
    "phrase",
    "vocab",
    "text_range",
    "multi_text",
]


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class FavoriteCreateRequest(BaseModel):
    """POST /favorites — add a favorite."""

    analysis_record_id: UUID | None = Field(default=None)
    target_type: FavoriteTargetType = Field(default="analysis_record")
    target_key: str = Field(min_length=1, max_length=256)
    payload_json: dict = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def validate_text_range_payload(self):
        if self.target_type not in {"text_range", "multi_text"}:
            return self
        if self.analysis_record_id is None:
            raise ValueError("analysis_record_id is required for text anchors")

        if self.target_type == "multi_text":
            segments = self.payload_json.get("segments")
            if not isinstance(segments, list) or len(segments) < 2:
                raise ValueError("payload_json.segments is required for multi_text favorites")
            for segment in segments:
                if not isinstance(segment, dict):
                    raise ValueError("payload_json.segments must contain objects")
                sentence_id = segment.get("sentence_id")
                selected_text = segment.get("selected_text")
                start_offset = segment.get("start_offset")
                end_offset = segment.get("end_offset")
                text_hash = segment.get("text_hash")
                if not isinstance(sentence_id, str) or not sentence_id.strip():
                    raise ValueError("multi_text segment sentence_id is required")
                if not isinstance(selected_text, str) or not selected_text.strip():
                    raise ValueError("multi_text segment selected_text is required")
                if not isinstance(start_offset, int) or not isinstance(end_offset, int):
                    raise ValueError("multi_text segment offsets are required")
                if start_offset < 0 or start_offset >= end_offset:
                    raise ValueError("multi_text segment offsets are invalid")
                if not isinstance(text_hash, str) or not text_hash.strip():
                    raise ValueError("multi_text segment text_hash is required")
                if utf16_code_unit_length(selected_text) != end_offset - start_offset:
                    raise ValueError("multi_text segment selected_text UTF-16 length must match offsets")
                if text_hash != compute_text_range_hash(selected_text):
                    raise ValueError("multi_text segment text_hash must match selected_text")
            return self

        sentence_id = self.payload_json.get("sentence_id")
        selected_text = self.payload_json.get("selected_text")
        start_offset = self.payload_json.get("start_offset")
        end_offset = self.payload_json.get("end_offset")
        text_hash = self.payload_json.get("text_hash")

        if not isinstance(sentence_id, str) or not sentence_id.strip():
            raise ValueError("payload_json.sentence_id is required for text_range favorites")
        if not isinstance(selected_text, str) or not selected_text.strip():
            raise ValueError("payload_json.selected_text is required for text_range favorites")
        if not isinstance(start_offset, int) or not isinstance(end_offset, int):
            raise ValueError("payload_json.start_offset and payload_json.end_offset are required for text_range favorites")
        if start_offset < 0 or start_offset >= end_offset:
            raise ValueError("text_range favorite offsets are invalid")
        if not isinstance(text_hash, str) or not text_hash.strip():
            raise ValueError("payload_json.text_hash is required for text_range favorites")
        if utf16_code_unit_length(selected_text) != end_offset - start_offset:
            raise ValueError("payload_json.selected_text UTF-16 length must match offsets")
        if text_hash != compute_text_range_hash(selected_text):
            raise ValueError("payload_json.text_hash must match selected_text")
        return self


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------


class FavoriteResponse(BaseModel):
    """Single favorite record."""

    id: UUID
    user_id: UUID
    target_type: FavoriteTargetType
    target_key: str
    analysis_record_id: UUID | None
    payload_json: dict
    note: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FavoriteListResponse(BaseModel):
    """GET /favorites — list."""

    items: list[FavoriteResponse]
    total: int


class FavoriteDeleteResponse(BaseModel):
    """DELETE /favorites/{analysis_record_id} — result."""

    deleted: bool


class FavoriteCreateResponse(BaseModel):
    """POST /favorites — add result."""

    id: str
    ok: bool
