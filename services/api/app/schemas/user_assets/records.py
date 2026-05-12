"""
User Assets API Schemas: Analysis Records.

Defines request/response Pydantic models for /records endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import SourceType


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class RecordCreateRequest(BaseModel):
    """POST /records — create/save an analysis record."""

    client_record_id: str = Field(min_length=1, max_length=64)
    source_type: SourceType = Field(default="user_input")
    title: str | None = Field(default=None, max_length=256)
    source_text: str = Field(min_length=1)
    source_text_hash: str = Field(min_length=1, max_length=64)
    request_payload_json: dict[str, Any] = Field(default_factory=dict)
    render_scene_json: dict[str, Any] = Field(default_factory=dict)
    page_state_json: dict[str, Any] = Field(default_factory=dict)
    reading_goal: str | None = Field(default=None)
    reading_variant: str | None = Field(default=None)
    extended: bool = Field(default=False)
    user_facing_state: str | None = Field(default=None)
    workflow_version: str | None = Field(default=None)
    schema_version: str | None = Field(default=None)
    analysis_status: str = Field(default="ready")


class RecordUpdateRequest(BaseModel):
    """PATCH /records/{id} — partial update."""

    title: str | None = Field(default=None, max_length=256)
    render_scene_json: dict[str, Any] | None = None
    page_state_json: dict[str, Any] | None = None
    user_facing_state: str | None = None
    extended: bool | None = None
    analysis_status: str | None = None
    is_favorited: bool | None = None
    last_opened_at: datetime | None = None


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------


class RecordResponse(BaseModel):
    """Single analysis record."""

    id: UUID
    user_id: UUID
    client_record_id: str | None
    source_type: SourceType
    title: str | None
    source_text: str
    source_text_hash: str
    request_payload_json: dict[str, Any] = Field(default_factory=dict)
    render_scene_json: dict[str, Any] = Field(default_factory=dict)
    page_state_json: dict[str, Any] = Field(default_factory=dict)
    reading_goal: str | None = Field(default=None)
    reading_variant: str | None = Field(default=None)
    extended: bool = Field(default=False)
    user_facing_state: str | None = Field(default=None)
    workflow_version: str | None = Field(default=None)
    schema_version: str | None = Field(default=None)
    analysis_status: str
    last_opened_at: datetime | None = Field(default=None)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecordListResponse(BaseModel):
    """GET /records — paginated list."""

    items: list[RecordResponse]
    total: int
    page: int
    limit: int


class RecordUpsertResponse(BaseModel):
    """POST /records — upsert result."""

    id: UUID
    client_record_id: str
    created: bool  # True if new, False if updated
    updated_at: datetime


class RecordDeleteResponse(BaseModel):
    """DELETE /records/{record_id} — result."""

    deleted: bool
