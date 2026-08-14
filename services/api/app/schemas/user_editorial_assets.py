from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.anchor_validation import validate_text_anchor_payload
from app.contracts.annotation import (
    TEXT_RANGE_HASH_ALGORITHM,
    TEXT_RANGE_OFFSET_UNIT,
)

UserEditorialAssetScope = Literal[
    "stable_source",
    "translation",
    "system_ai_layer",
    "ask_supplement",
]


class UserEditorialAssetAnchorRange(BaseModel):
    """Schema-only range item for future multi-anchor assets."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    anchor_segment_id: str = Field(min_length=1)
    offset_unit: Literal["utf16"] = TEXT_RANGE_OFFSET_UNIT
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    selected_text: str = Field(min_length=1)
    text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")
    hash_algorithm: Literal["fnv1a32-utf16"] = TEXT_RANGE_HASH_ALGORITHM

    @model_validator(mode="after")
    def validate_span(self) -> UserEditorialAssetAnchorRange:
        validate_text_anchor_payload(
            offset_unit=self.offset_unit,
            start_offset=self.start_offset,
            end_offset=self.end_offset,
            selected_text=self.selected_text,
            text_hash=self.text_hash,
            hash_algorithm=self.hash_algorithm,
        )
        return self


class UserEditorialAssetAnchorSet(BaseModel):
    """Schema-only draft for future multi_text editorial assets.

    V1c production writes remain single-range first. This DTO exists so future
    multi-range work does not overload UserEditorialAssetAnchor or fall back to
    legacy render_scene sentence/target_key validation.
    """

    model_config = ConfigDict(extra="forbid")

    anchor_mode: Literal["multi_text"] = "multi_text"
    record_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    scope: UserEditorialAssetScope = "stable_source"
    ranges: list[UserEditorialAssetAnchorRange] = Field(min_length=2)


class UserEditorialAssetAnchor(BaseModel):
    """Draft anchor contract for future user editorial assets.

    This stays schema-only for now. Runtime writes remain on the legacy
    `user_annotations` / `reader_notes` paths because this contract has no runtime wiring.
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
        validate_text_anchor_payload(
            offset_unit=self.offset_unit,
            start_offset=self.start_offset,
            end_offset=self.end_offset,
            selected_text=self.selected_text,
            text_hash=self.text_hash,
            hash_algorithm=self.hash_algorithm,
        )
        return self
