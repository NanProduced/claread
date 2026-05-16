from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ExcerptAssetState = Literal["all", "favorite", "highlight", "note", "insight"]
ExcerptAnchorType = Literal["sentence", "text_range", "multi_text"]
ExcerptInsightType = Literal[
    "grammar",
    "sentence",
    "term",
    "logic",
    "interpretation",
    "summary",
]


class ExcerptInsight(BaseModel):
    id: str
    type: ExcerptInsightType
    label: str
    title: str
    detail: str | None = None


class ExcerptAssetSegment(BaseModel):
    paragraph_id: str | None = None
    sentence_id: str
    selected_text: str
    start_offset: int
    end_offset: int
    text_hash: str


class ExcerptAssetItem(BaseModel):
    target_key: str
    anchor_type: ExcerptAnchorType
    sentence_id: str | None = None
    selected_text: str
    translation: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    text_hash: str | None = None
    segments: list[ExcerptAssetSegment] = Field(default_factory=list)
    updated_at: str
    is_favorited: bool
    is_highlighted: bool
    is_noted: bool
    annotation_id: str | None = None
    annotation_type: str | None = None
    annotation_color: str | None = None
    note: str | None = None
    insights: list[ExcerptInsight] = Field(default_factory=list)


class ExcerptAssetGroup(BaseModel):
    record_id: str
    client_record_id: str | None = None
    title: str
    subtitle: str | None = None
    updated_at: str
    asset_count: int
    items: list[ExcerptAssetItem] = Field(default_factory=list)


class ExcerptAssetsResponse(BaseModel):
    groups: list[ExcerptAssetGroup]
    total_assets: int
    total_groups: int
    page: int
    limit: int
