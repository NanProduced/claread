from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field, model_validator


USER_ANNOTATION_COLOR_PATTERN = (
    "^(soft_green|soft_blue|soft_purple|warm_yellow|sage_green)$"
)


class UserAnnotationCreateRequest(BaseModel):
    analysis_record_id: Optional[str] = None
    annotation_type: str = Field(default="highlight", pattern="^(highlight|note)$")
    anchor_type: str = Field(default="sentence", pattern="^(sentence|paragraph|text_range)$")
    target_key: Optional[str] = None
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    selected_text: str
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    text_hash: Optional[str] = None
    color: str = Field(default="soft_green", pattern=USER_ANNOTATION_COLOR_PATTERN)
    note: Optional[str] = None
    payload_json: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_anchor_fields(self):
        if not self.selected_text.strip():
            raise ValueError("selected_text must not be empty")

        if self.anchor_type == "sentence":
            if not self.sentence_id:
                raise ValueError("sentence_id is required for sentence anchors")
            return self

        if self.anchor_type == "paragraph":
            if not self.paragraph_id:
                raise ValueError("paragraph_id is required for paragraph anchors")
            return self

        if not self.sentence_id:
            raise ValueError("sentence_id is required for text_range anchors")
        if self.start_offset is None or self.end_offset is None:
            raise ValueError("start_offset and end_offset are required for text_range anchors")
        if self.start_offset < 0 or self.end_offset < 0:
            raise ValueError("text_range offsets must be non-negative")
        if self.start_offset >= self.end_offset:
            raise ValueError("start_offset must be less than end_offset")
        if not self.text_hash:
            raise ValueError("text_hash is required for text_range anchors")
        return self


class UserAnnotationUpdateRequest(BaseModel):
    color: Optional[str] = Field(default=None, pattern=USER_ANNOTATION_COLOR_PATTERN)
    note: Optional[str] = None


class UserAnnotationResponse(BaseModel):
    id: UUID
    analysis_record_id: Optional[UUID] = None
    annotation_type: str
    anchor_type: str
    target_key: str
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    selected_text: str
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    text_hash: Optional[str] = None
    color: str
    note: Optional[str] = None
    payload_json: dict
    created_at: str
    updated_at: str


class UserAnnotationListResponse(BaseModel):
    items: list[UserAnnotationResponse]
