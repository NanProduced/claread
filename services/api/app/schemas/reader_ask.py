from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.user_assets.favorites import FavoriteTargetType

ReaderAskAnchorType = Literal[
    "sentence",
    "text_range",
    "multi_text",
    "sentence_entry",
    "user_annotation",
    "favorite",
    "dictionary_entry",
]
ReaderAskMessageRole = Literal["user", "assistant", "system"]
ReaderAskMessageStatus = Literal["pending", "streaming", "completed", "failed"]
ReaderAskCitationKind = Literal[
    "anchor",
    "record_excerpt_asset",
    "user_excerpt_asset",
    "vocabulary",
    "dictionary_entry",
    "dictionary_ai",
]
ReaderAskActionType = Literal[
    "save_note",
    "save_excerpt",
    "favorite_anchor",
    "save_answer_note",
]
ReaderAskActionStatus = Literal["pending", "confirmed", "executed", "rejected"]
ReaderAskToolStatus = Literal["started", "completed", "failed"]
ReaderAskTaskMode = Literal["explain", "breakdown", "vocabulary", "grammar", "practice"]
ReaderAskResponseCardType = Literal[
    "sentence_breakdown_card",
    "vocabulary_in_context_card",
    "practice_card",
]


class ReaderAskAnchorSegment(BaseModel):
    paragraph_id: str | None = None
    sentence_id: str
    selected_text: str
    start_offset: int
    end_offset: int
    text_hash: str


class ReaderAskAnchorRef(BaseModel):
    anchor_type: ReaderAskAnchorType
    anchor_id: str | None = None
    target_key: str | None = None
    target_type: FavoriteTargetType | None = None
    sentence_id: str | None = None
    paragraph_id: str | None = None
    entry_type: str | None = None
    label: str | None = None
    selected_text: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    text_hash: str | None = None
    dict_entry_id: int | None = None
    query: str | None = None
    note: str | None = None
    segments: list[ReaderAskAnchorSegment] = Field(default_factory=list)
    payload_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_anchor_fields(self) -> ReaderAskAnchorRef:
        if self.anchor_type == "multi_text" and len(self.segments) < 2:
            raise ValueError("multi_text anchors require at least two segments")
        if self.anchor_type == "text_range":
            required = [
                self.sentence_id,
                self.selected_text,
                self.text_hash,
            ]
            if any(value is None or (isinstance(value, str) and not value.strip()) for value in required):
                raise ValueError("text_range anchors require sentence_id, selected_text, and text_hash")
        if self.anchor_type == "dictionary_entry" and self.dict_entry_id is None and not self.query:
            raise ValueError("dictionary_entry anchors require dict_entry_id or query")
        return self


class ReaderAskReaderFocus(BaseModel):
    sentence_id: str | None = None
    paragraph_id: str | None = None
    selected_text: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    text_hash: str | None = None


class ReaderAskCitation(BaseModel):
    citation_id: str
    kind: ReaderAskCitationKind
    label: str
    anchor_type: ReaderAskAnchorType | None = None
    sentence_id: str | None = None
    target_key: str | None = None
    selected_text: str | None = None
    record_id: str | None = None
    source_article_title: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ReaderAskActionProposal(BaseModel):
    id: str
    action_type: ReaderAskActionType
    label: str
    description: str | None = None
    requires_confirmation: bool = True
    status: ReaderAskActionStatus = "pending"
    payload_json: dict[str, Any] = Field(default_factory=dict)


class ReaderAskToolTraceEntry(BaseModel):
    tool_name: str
    status: ReaderAskToolStatus
    started_at: str | None = None
    completed_at: str | None = None
    summary: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ReaderAskResolvedContextSummary(BaseModel):
    record_id: str
    record_title: str | None = None
    anchor_count: int = 0
    used_history_lookup: bool = False
    current_sentence_used: bool = False
    current_paragraph_used: bool = False
    used_record_assets: bool = False
    used_dictionary: bool = False
    source_labels: list[str] = Field(default_factory=list)


class ReaderAskSentenceBreakdownPart(BaseModel):
    label: str
    text: str
    note: str | None = None


class ReaderAskSentenceBreakdownCard(BaseModel):
    card_type: Literal["sentence_breakdown_card"] = "sentence_breakdown_card"
    sentence_text: str
    translation_zh: str | None = None
    main_clause: str | None = None
    analysis_zh: str | None = None
    parts: list[ReaderAskSentenceBreakdownPart] = Field(default_factory=list)


class ReaderAskVocabularyInContextCard(BaseModel):
    card_type: Literal["vocabulary_in_context_card"] = "vocabulary_in_context_card"
    query: str
    display_word: str | None = None
    phonetic: str | None = None
    meaning_zh: str | None = None
    why_here: str | None = None
    translation_zh: str | None = None
    learning_tip: str | None = None
    source_sentence: str | None = None


class ReaderAskPracticeCard(BaseModel):
    card_type: Literal["practice_card"] = "practice_card"
    title: str
    prompt: str
    expected_focus: str | None = None
    hints: list[str] = Field(default_factory=list)
    answer_guidance: str | None = None
    source_sentence: str | None = None


ReaderAskResponseCard = Annotated[
    ReaderAskSentenceBreakdownCard | ReaderAskVocabularyInContextCard | ReaderAskPracticeCard,
    Field(discriminator="card_type"),
]


class ReaderAskMessage(BaseModel):
    id: str
    thread_id: str
    role: ReaderAskMessageRole
    status: ReaderAskMessageStatus
    content_md: str
    task_mode: ReaderAskTaskMode | None = None
    context_anchors: list[ReaderAskAnchorRef] = Field(default_factory=list)
    citations: list[ReaderAskCitation] = Field(default_factory=list)
    action_proposals: list[ReaderAskActionProposal] = Field(default_factory=list)
    tool_trace: list[ReaderAskToolTraceEntry] = Field(default_factory=list)
    response_cards: list[ReaderAskResponseCard] = Field(default_factory=list)
    resolved_context: ReaderAskResolvedContextSummary | None = None
    usage_event_id: str | None = None
    created_at: str
    updated_at: str


class ReaderAskThreadSummary(BaseModel):
    id: str
    record_id: str
    title: str | None = None
    is_default: bool
    archived_at: str | None = None
    created_at: str
    updated_at: str
    last_message_at: str | None = None


class ReaderAskThreadDetail(ReaderAskThreadSummary):
    messages: list[ReaderAskMessage] = Field(default_factory=list)


class ReaderAskThreadListResponse(BaseModel):
    items: list[ReaderAskThreadSummary]


class ReaderAskThreadCreateRequest(BaseModel):
    record_id: str
    mode: Literal["default", "new_chat"] = "default"
    title: str | None = Field(default=None, max_length=120)


class ReaderAskActionConfirmRequest(BaseModel):
    confirmed: bool = True


class ReaderAskActionConfirmResponse(BaseModel):
    ok: bool
    action_id: str
    status: ReaderAskActionStatus
    result: dict[str, Any] = Field(default_factory=dict)


class ReaderAskMessageStreamRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    task_mode: ReaderAskTaskMode = "explain"
    anchors: list[ReaderAskAnchorRef] = Field(default_factory=list)
    reader_focus: ReaderAskReaderFocus | None = None


class ReaderAskCompletedPayload(BaseModel):
    id: str
    thread_id: str
    content_md: str
    task_mode: ReaderAskTaskMode
    citations: list[ReaderAskCitation] = Field(default_factory=list)
    action_proposals: list[ReaderAskActionProposal] = Field(default_factory=list)
    tool_trace: list[ReaderAskToolTraceEntry] = Field(default_factory=list)
    response_cards: list[ReaderAskResponseCard] = Field(default_factory=list)
    usage_summary: dict[str, Any] | None = None
    billed_points: int = 0
    resolved_context: ReaderAskResolvedContextSummary


class ReaderAskStreamEnvelope(BaseModel):
    event: str
    data: dict[str, Any]
