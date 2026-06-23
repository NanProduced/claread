from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.annotation import (
    TEXT_RANGE_HASH_ALGORITHM,
    TEXT_RANGE_OFFSET_UNIT,
    compute_text_range_hash,
    utf16_code_unit_length,
)

UserEditorialAssetScope = Literal[
    "stable_source",
    "translation",
    "system_ai_layer",
    "ask_supplement",
]


class UserEditorialAssetAnchor(BaseModel):
    """Draft D6-U0 anchor contract for future user editorial assets.

    This stays schema-only for now. Runtime writes remain on the legacy
    `user_annotations` / `reader_notes` paths until D6-U1 wiring lands.
    """

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    unit_id: str = Field(min_length=1)
    anchor_segment_id: str = Field(min_length=1)
    scope: UserEditorialAssetScope = "stable_source"
    offset_unit: Literal["utf16"] = TEXT_RANGE_OFFSET_UNIT
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    selected_text: str = Field(min_length=1)
    text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")
    hash_algorithm: Literal["fnv1a32-utf16"] = TEXT_RANGE_HASH_ALGORITHM

    @model_validator(mode="after")
    def validate_span(self) -> UserEditorialAssetAnchor:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        if utf16_code_unit_length(self.selected_text) != self.end_offset - self.start_offset:
            raise ValueError("selected_text UTF-16 length must match offset span")
        if compute_text_range_hash(self.selected_text) != self.text_hash:
            raise ValueError("text_hash must match selected_text")
        return self
