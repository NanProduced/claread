"""
User Assets API Schemas: Favorites.

Defines request/response Pydantic models for /favorites endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

FavoriteTargetType = Literal["analysis_record", "daily_reader_article"]


class FavoriteCreateRequest(BaseModel):
    analysis_record_id: UUID | None = Field(default=None)
    target_type: FavoriteTargetType = Field(default="analysis_record")
    target_key: str = Field(min_length=1, max_length=256)
    payload_json: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_article_favorite_payload(self):
        if self.target_type == "analysis_record" and self.analysis_record_id is None:
            raise ValueError("analysis_record_id is required for analysis_record favorites")
        return self


class FavoriteResponse(BaseModel):
    id: UUID
    user_id: UUID
    target_type: FavoriteTargetType
    target_key: str
    analysis_record_id: UUID | None
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
