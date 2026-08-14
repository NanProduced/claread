"""
User Assets API Schemas: Favorites.

Defines request/response Pydantic models for /favorites endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Exact favorite target union. No bare strings,
# no legacy targets, no aliases.
FavoriteTargetType = Literal["daily_reader_article", "reading_record"]


class FavoriteCreateRequest(BaseModel):
    target_type: FavoriteTargetType
    target_key: str = Field(min_length=1, max_length=256)
    payload_json: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reading_record_target_key(self):
        if self.target_type == "reading_record":
            try:
                UUID(self.target_key)
            except ValueError as exc:
                raise ValueError(
                    "reading_record favorites require a reading_record_id target_key"
                ) from exc
        return self


class FavoriteResponse(BaseModel):
    id: UUID
    user_id: UUID
    target_type: FavoriteTargetType
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
