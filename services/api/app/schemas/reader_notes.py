from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.user_annotations import UserAnnotationSegment


class ReaderNoteCreateRequest(BaseModel):
    analysis_record_id: str
    quote_mode: str = Field(pattern="^(sentence|text_range|multi_text)$")
    anchor_sentence_id: str
    target_key: Optional[str] = None
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    selected_text: str
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    text_hash: Optional[str] = None
    segments: list[UserAnnotationSegment] = Field(default_factory=list)
    note_text: str = Field(min_length=1)
    payload_json: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_quote_fields(self):
        if not self.anchor_sentence_id.strip():
            raise ValueError("anchor_sentence_id is required")
        if not self.selected_text.strip():
            raise ValueError("selected_text must not be empty")
        if not self.note_text.strip():
            raise ValueError("note_text must not be empty")
        if self.quote_mode == "sentence":
            if not self.sentence_id:
                raise ValueError("sentence_id is required for sentence notes")
            return self
        if self.quote_mode == "multi_text":
            if len(self.segments) < 2:
                raise ValueError("multi_text notes require at least two segments")
            return self
        if not self.sentence_id:
            raise ValueError("sentence_id is required for text_range notes")
        if self.start_offset is None or self.end_offset is None or not self.text_hash:
            raise ValueError("text_range notes require offsets and text_hash")
        return self


class ReaderNoteUpdateRequest(BaseModel):
    note_text: str = Field(min_length=1)


class ReaderNoteResponse(BaseModel):
    id: UUID
    analysis_record_id: UUID
    anchor_sentence_id: str
    quote_mode: str
    target_key: str
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    selected_text: str
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    text_hash: Optional[str] = None
    segments: list[UserAnnotationSegment] = Field(default_factory=list)
    note_text: str
    payload_json: dict
    created_at: str
    updated_at: str


class ReaderNoteListResponse(BaseModel):
    items: list[ReaderNoteResponse]
