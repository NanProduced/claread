from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.user_annotations import UserAnnotationSegment
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor


class ReaderNoteCreateRequest(BaseModel):
    # DATA-LEGACY-IDENTITY-EXIT: the Reading Record anchor is the only note
    # contract; the Reading Record anchor gate is the sole validation
    # authority. Legacy analysis_record / render_scene fields are gone.
    anchor: UserEditorialAssetAnchor
    selected_text: str
    note_text: str = Field(min_length=1)
    payload_json: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_quote_fields(self):
        if not self.selected_text.strip():
            raise ValueError("selected_text must not be empty")
        if self.selected_text != self.anchor.selected_text:
            raise ValueError("selected_text must match anchor.selected_text")
        if not self.note_text.strip():
            raise ValueError("note_text must not be empty")
        return self


class ReaderNoteUpdateRequest(BaseModel):
    note_text: str = Field(min_length=1)


class ReaderNoteResponse(BaseModel):
    id: UUID
    quote_mode: str
    target_key: str
    paragraph_id: str | None = None
    sentence_id: str | None = None
    selected_text: str
    start_offset: int | None = None
    end_offset: int | None = None
    text_hash: str | None = None
    segments: list[UserAnnotationSegment] = Field(default_factory=list)
    note_text: str
    payload_json: dict
    created_at: str
    updated_at: str
    # D6-U4 Reading Record anchor columns — the only anchor identity.
    reading_record_id: UUID | None = None
    base_id: UUID | None = None
    generation: int | None = None
    unit_id: str | None = None
    anchor_segment_id: str | None = None
    unit_start_utf16: int | None = None
    unit_end_utf16: int | None = None


class ReaderNoteListResponse(BaseModel):
    items: list[ReaderNoteResponse]
