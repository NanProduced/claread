from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


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
