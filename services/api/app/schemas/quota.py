"""Quota API Schemas.

Defines request/response Pydantic models for /me/quota endpoints.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class QuotaResponse(BaseModel):
    daily_free_points: int
    daily_used_points: int
    bonus_points: int
    remaining_points: int


class AnonymousQuotaResponse(BaseModel):
    remaining_trials: int
    max_trials_per_day: int
    reset_at: str


class QuotaCheckRequest(BaseModel):
    anonymous_id: str | None = None


class QuotaCheckResponse(BaseModel):
    allowed: bool
    remaining: int
    reset_at: str
    quota_type: str


class LedgerEntryResponse(BaseModel):
    id: str
    entry_type: str
    points: int
    bucket_type: str
    balance_after: int
    description: str
    article_title: str | None
    task_id: str | None = None
    created_at: datetime


class LedgerListResponse(BaseModel):
    items: list[LedgerEntryResponse]
    cursor: str | None
    has_more: bool
