from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.user_annotations import UserAnnotationSegment
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor


class ReaderNoteCreateRequest(BaseModel):
    # D6-A5 dual-contract spike: `analysis_record_id` is now optional. When
    # the new `anchor` contract is supplied, legacy sentence_id / offsets /
    # text_hash are also optional and the request is routed to the Reading
    # Record anchor gate. The legacy `analysis_record_id` is NEVER auto-filled
    # from the new anchor — see schema-and-domain-contract.md D6-A5.
    analysis_record_id: str | None = None
    quote_mode: str = Field(pattern="^(sentence|text_range|multi_text)$")
    anchor_sentence_id: str | None = None
    target_key: str | None = None
    paragraph_id: str | None = None
    sentence_id: str | None = None
    selected_text: str
    start_offset: int | None = None
    end_offset: int | None = None
    text_hash: str | None = None
    segments: list[UserAnnotationSegment] = Field(default_factory=list)
    note_text: str = Field(min_length=1)
    payload_json: dict = Field(default_factory=dict)
    anchor: UserEditorialAssetAnchor | None = None

    @model_validator(mode="after")
    def validate_quote_fields(self):
        if self.anchor is not None:
            # D6-A5 dual-contract path. Legacy fields become optional; the
            # Reading Record anchor gate does the authoritative validation.
            if not self.selected_text.strip():
                raise ValueError("selected_text must not be empty")
            if self.selected_text != self.anchor.selected_text:
                raise ValueError("selected_text must match anchor.selected_text")
            if not self.note_text.strip():
                raise ValueError("note_text must not be empty")
            return self

        # Legacy single-contract path — unchanged from previous behaviour.
        if not self.analysis_record_id:
            raise ValueError("analysis_record_id is required")
        if not self.anchor_sentence_id or not self.anchor_sentence_id.strip():
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
    analysis_record_id: UUID | None = None
    anchor_sentence_id: str | None = None
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
    # D6-U4 Reading Record anchor columns. Populated for new Reading Record
    # rows; None for legacy analysis_record_id rows.
    reading_record_id: UUID | None = None
    base_id: UUID | None = None
    generation: int | None = None
    unit_id: str | None = None
    anchor_segment_id: str | None = None
    unit_start_utf16: int | None = None
    unit_end_utf16: int | None = None


class ReaderNoteListResponse(BaseModel):
    items: list[ReaderNoteResponse]
