"""
User Assets API Schemas: Favorites.

Defines request/response Pydantic models for /favorites endpoints.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class FavoriteCreateRequest(BaseModel):
    """POST /favorites — add a favorite."""

    analysis_record_id: UUID | None = Field(default=None)
    target_type: str = Field(default="analysis_record")
    target_key: str = Field(min_length=1, max_length=256)
    payload_json: dict = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=1024)


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------


class FavoriteResponse(BaseModel):
    """Single favorite record."""

    id: UUID
    user_id: UUID
    target_type: str
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
