from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length


USER_ANNOTATION_COLOR_PATTERN = (
    "^(soft_green|soft_blue|soft_purple|warm_yellow|sage_green)$"
)


class UserAnnotationSegment(BaseModel):
    paragraph_id: Optional[str] = None
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
    analysis_record_id: Optional[str] = None
    anchor_type: str = Field(default="sentence", pattern="^(sentence|text_range|multi_text)$")
    target_key: Optional[str] = None
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    selected_text: str
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    text_hash: Optional[str] = None
    segments: list[UserAnnotationSegment] = Field(default_factory=list)
    color: str = Field(default="soft_green", pattern=USER_ANNOTATION_COLOR_PATTERN)
    payload_json: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_anchor_fields(self):
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
    analysis_record_id: Optional[UUID] = None
    anchor_type: str
    target_key: str
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    selected_text: str
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    text_hash: Optional[str] = None
    segments: list[UserAnnotationSegment] = Field(default_factory=list)
    color: str
    payload_json: dict
    created_at: str
    updated_at: str


class UserAnnotationListResponse(BaseModel):
    items: list[UserAnnotationResponse]
