"""
User Assets API Schemas: Favorites.

Defines request/response Pydantic models for /favorites endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# DATA-LEGACY-IDENTITY-EXIT: only the Daily Reader article target remains.
# Response rows keep a plain str target_type because historical rows may
# still carry legacy values; the API never creates them anymore.
FavoriteTargetType = Literal["daily_reader_article"]


class FavoriteCreateRequest(BaseModel):
    target_type: FavoriteTargetType = Field(default="daily_reader_article")
    target_key: str = Field(min_length=1, max_length=256)
    payload_json: dict = Field(default_factory=dict)


class FavoriteResponse(BaseModel):
    id: UUID
    user_id: UUID
    target_type: str
    target_key: str
    payload_json: dict
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FavoriteListResponse(BaseModel):
    items: list[FavoriteResponse]
    total: int


class FavoriteDeleteResponse(BaseModel):
    deleted: bool


class FavoriteCreateResponse(BaseModel):
    id: str
    ok: bool
