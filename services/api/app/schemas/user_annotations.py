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
    # DATA-LEGACY-IDENTITY-EXIT: the Reading Record anchor is the only
    # highlight contract. Legacy analysis_record / render_scene fields are
    # gone; the anchor gate is the sole validation authority.
    anchor: UserEditorialAssetAnchor
    selected_text: str
    color: str = Field(default="warm_yellow", pattern=USER_ANNOTATION_COLOR_PATTERN)
    payload_json: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_anchor_fields(self):
        if not self.selected_text.strip():
            raise ValueError("selected_text must not be empty")
        if self.selected_text != self.anchor.selected_text:
            raise ValueError("selected_text must match anchor.selected_text")
        return self


class UserAnnotationUpdateRequest(BaseModel):
    color: str = Field(pattern=USER_ANNOTATION_COLOR_PATTERN)


class UserAnnotationResponse(BaseModel):
    id: UUID
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
    # Reading Record anchor columns — the only anchor identity.
    reading_record_id: UUID | None = None
    base_id: UUID | None = None
    generation: int | None = None
    unit_id: str | None = None
    anchor_segment_id: str | None = None
    unit_start_utf16: int | None = None
    unit_end_utf16: int | None = None


class UserAnnotationListResponse(BaseModel):
    items: list[UserAnnotationResponse]
