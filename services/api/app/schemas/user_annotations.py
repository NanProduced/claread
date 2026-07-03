from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor

USER_ANNOTATION_COLOR_PATTERN = (
    "^(warm_yellow|soft_mint|soft_rose)$"
)


class UserAnnotationSegment(BaseModel):
    paragraph_id: str | None = None
    sentence_id: str
    selected_text: str
    start_offset: int
    end_offset: int
    text_hash: str

    @model_validator(mode="after")
    def validate_segment(self):
        if not self.sentence_id.strip():
            raise ValueError("sentence_id is required for anchor segments")
        if not self.selected_text.strip():
            raise ValueError("selected_text must not be empty in anchor segments")
        if self.start_offset < 0 or self.end_offset < 0:
            raise ValueError("anchor segment offsets must be non-negative")
        if self.start_offset >= self.end_offset:
            raise ValueError("anchor segment start_offset must be less than end_offset")
        if utf16_code_unit_length(self.selected_text) != self.end_offset - self.start_offset:
            raise ValueError("anchor segment UTF-16 length must match start_offset/end_offset")
        if self.text_hash != compute_text_range_hash(self.selected_text):
            raise ValueError("anchor segment text_hash must match selected_text")
        return self


class UserAnnotationCreateRequest(BaseModel):
    analysis_record_id: str | None = None
    anchor_type: str = Field(default="sentence", pattern="^(sentence|text_range|multi_text)$")
    target_key: str | None = None
    paragraph_id: str | None = None
    sentence_id: str | None = None
    selected_text: str
    start_offset: int | None = None
    end_offset: int | None = None
    text_hash: str | None = None
    segments: list[UserAnnotationSegment] = Field(default_factory=list)
    color: str = Field(default="warm_yellow", pattern=USER_ANNOTATION_COLOR_PATTERN)
    payload_json: dict = Field(default_factory=dict)
    # D6-A5 dual-contract spike. When set, the legacy sentence_id / offsets /
    # text_hash fields become optional and the request is routed to the
    # Reading Record anchor gate. The legacy `analysis_record_id` field is
    # explicitly NOT auto-populated from the new anchor — legacy writes must
    # never silently masquerade a Reading Record id as a `analysis_record_id`.
    anchor: UserEditorialAssetAnchor | None = None

    @model_validator(mode="after")
    def validate_anchor_fields(self):
        if self.anchor is not None:
            # When the new anchor contract is supplied, the legacy fields are
            # optional — service-side projection derives what it needs from
            # `anchor`. selected_text is still required because the contract
            # shape echoes it; the gate will re-validate hash and offsets.
            if not self.selected_text.strip():
                raise ValueError("selected_text must not be empty")
            if self.selected_text != self.anchor.selected_text:
                raise ValueError("selected_text must match anchor.selected_text")
            return self

        if not self.selected_text.strip():
            raise ValueError("selected_text must not be empty")

        if self.anchor_type == "sentence":
            if not self.sentence_id:
                raise ValueError("sentence_id is required for sentence anchors")
            return self

        if self.anchor_type == "multi_text":
            if not self.analysis_record_id:
                raise ValueError("analysis_record_id is required for multi_text anchors")
            if len(self.segments) < 2:
                raise ValueError("multi_text anchors require at least two segments")
            return self

        if not self.sentence_id:
            raise ValueError("sentence_id is required for text_range anchors")
        if not self.analysis_record_id:
            raise ValueError("analysis_record_id is required for text_range anchors")
        if self.start_offset is None or self.end_offset is None:
            raise ValueError("start_offset and end_offset are required for text_range anchors")
        if self.start_offset < 0 or self.end_offset < 0:
            raise ValueError("text_range offsets must be non-negative")
        if self.start_offset >= self.end_offset:
            raise ValueError("start_offset must be less than end_offset")
        if not self.text_hash:
            raise ValueError("text_hash is required for text_range anchors")
        if utf16_code_unit_length(self.selected_text) != self.end_offset - self.start_offset:
            raise ValueError("selected_text UTF-16 length must match start_offset/end_offset")
        if self.text_hash != compute_text_range_hash(self.selected_text):
            raise ValueError("text_hash must match selected_text")
        return self


class UserAnnotationUpdateRequest(BaseModel):
    color: str = Field(pattern=USER_ANNOTATION_COLOR_PATTERN)


class UserAnnotationResponse(BaseModel):
    id: UUID
    analysis_record_id: UUID | None = None
    anchor_type: str
    target_key: str
    paragraph_id: str | None = None
    sentence_id: str | None = None
    selected_text: str
    start_offset: int | None = None
    end_offset: int | None = None
    text_hash: str | None = None
    segments: list[UserAnnotationSegment] = Field(default_factory=list)
    color: str = Field(pattern=USER_ANNOTATION_COLOR_PATTERN)
    payload_json: dict
    created_at: str
    updated_at: str
    superseded_ids: list[UUID] = Field(default_factory=list)
    # D6-U4 Reading Record anchor columns. Populated for new Reading Record
    # rows; None for legacy analysis_record_id rows.
    reading_record_id: UUID | None = None
    base_id: UUID | None = None
    generation: int | None = None
    unit_id: str | None = None
    anchor_segment_id: str | None = None
    unit_start_utf16: int | None = None
    unit_end_utf16: int | None = None


class UserAnnotationListResponse(BaseModel):
    items: list[UserAnnotationResponse]
