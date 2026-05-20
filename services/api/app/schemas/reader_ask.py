from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    "create_supplement_grammar_note",
]
ReaderAskActionStatus = Literal["pending", "confirmed", "executed", "rejected"]
ReaderAskToolStatus = Literal["started", "completed", "failed"]
ReaderAskTaskMode = Literal["explain", "breakdown", "vocabulary", "grammar", "practice"]
ReaderAskResolvedIntent = ReaderAskTaskMode
ReaderAskEntryAction = Literal[
    "ask_about_this",
    "explain_this",
    "why_here",
    "lookup_in_context",
    "compare_translation",
]
ReaderAskAttachmentKind = Literal[
    "text_selection",
    "annotation_ref",
    "analysis_ref",
    "supplement_ref",
    "record_ref",
]
ReaderAskResponseCardType = Literal[
    "sentence_breakdown_card",
    "vocabulary_in_context_card",
    "practice_card",
]
ReaderAskSupplementType = Literal["grammar_note"]


class ReaderAskAnchorSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    paragraph_id: str | None = None
    sentence_id: str
    selected_text: str
    start_offset: int
    end_offset: int
    text_hash: str


class ReaderAskAnchorRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

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


class ReaderAskPageIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    record_id: str
    title: str | None = None
    surface: Literal["reader"] = "reader"
    source: Literal["reader_2_0"] = "reader_2_0"
    available_context_capabilities: list[str] = Field(default_factory=list)
    has_article_overview: bool = False
    has_sentence_entries: bool = False
    has_annotations: bool = False
    has_user_assets: bool = False


class ReaderAskAttachmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    anchor_type: Literal["sentence", "text_range", "multi_text"]
    target_key: str | None = None
    record_id: str | None = None
    paragraph_id: str | None = None
    sentence_id: str | None = None
    selected_text: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    text_hash: str | None = None
    segments: list[ReaderAskAnchorSegment] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_payload(self) -> "ReaderAskAttachmentPayload":
        if self.anchor_type == "multi_text" and len(self.segments) < 2:
            raise ValueError("multi_text payloads require at least two segments")
        if self.anchor_type == "text_range":
            required = [self.sentence_id, self.selected_text, self.text_hash, self.start_offset, self.end_offset]
            if any(value is None or (isinstance(value, str) and not value.strip()) for value in required):
                raise ValueError("text_range payloads require sentence_id, selected_text, offsets, and text_hash")
        return self


class ReaderAskAttachmentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_surface: str
    entry_action: ReaderAskEntryAction | None = None
    sentence_id: str | None = None
    paragraph_id: str | None = None
    entry_id: str | None = None
    entry_type: str | None = None
    asset_id: str | None = None
    annotation_type: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    translation_zh: str | None = None
    note: str | None = None
    title: str | None = None
    query: str | None = None
    lookup_text: str | None = None
    visual_tone: str | None = None


class ReaderAskAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: ReaderAskAttachmentKind
    subtype: str
    label: str
    selected_text: str | None = None
    target_key: str | None = None
    anchor_payload: ReaderAskAttachmentPayload | None = None
    metadata: ReaderAskAttachmentMetadata


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
    explicit_attachment_count: int = 0
    used_history_lookup: bool = False
    current_sentence_used: bool = False
    current_paragraph_used: bool = False
    used_record_assets: bool = False
    used_dictionary: bool = False
    source_labels: list[str] = Field(default_factory=list)


class ReaderAskContextPlan(BaseModel):
    entry_action: ReaderAskEntryAction
    explicit_attachment_count: int = 0
    normalized_anchor_count: int = 0
    primary_anchor_type: ReaderAskAnchorType | None = None
    used_history_lookup: bool = False
    used_record_context: bool = False
    used_record_insights: bool = False
    used_dictionary: bool = False
    source_labels: list[str] = Field(default_factory=list)


class ReaderAskResolvedContextInput(BaseModel):
    page_identity: ReaderAskPageIdentity
    entry_action: ReaderAskEntryAction
    attachments: list[ReaderAskAttachment] = Field(default_factory=list)
    normalized_anchors: list[ReaderAskAnchorRef] = Field(default_factory=list)


class ReaderAskRunInfo(BaseModel):
    turn_id: str
    run_id: str
    run_attempt: int = 1
    supersedes_run_id: str | None = None


class ReaderAskSupplementCandidate(BaseModel):
    candidate_id: str
    supplement_type: ReaderAskSupplementType
    target_key: str
    sentence_id: str
    paragraph_id: str | None = None
    title: str
    content: str
    anchor: ReaderAskAnchorRef
    schema_version: str
    created_from_turn_run_id: str
    label: str = "AI 补充语法旁注"


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
    resolved_intent: ReaderAskResolvedIntent | None = None
    context_anchors: list[ReaderAskAnchorRef] = Field(default_factory=list)
    citations: list[ReaderAskCitation] = Field(default_factory=list)
    action_proposals: list[ReaderAskActionProposal] = Field(default_factory=list)
    tool_trace: list[ReaderAskToolTraceEntry] = Field(default_factory=list)
    response_cards: list[ReaderAskResponseCard] = Field(default_factory=list)
    resolved_context: ReaderAskResolvedContextSummary | None = None
    context_plan: ReaderAskContextPlan | None = None
    resolved_context_input: ReaderAskResolvedContextInput | None = None
    run_info: ReaderAskRunInfo | None = None
    supplement_candidates: list[ReaderAskSupplementCandidate] = Field(default_factory=list)
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
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=5000)
    page_identity: ReaderAskPageIdentity
    attachments: list[ReaderAskAttachment] = Field(default_factory=list)
    entry_action: ReaderAskEntryAction
    model: str | None = None


class ReaderAskCompletedPayload(BaseModel):
    id: str
    thread_id: str
    content_md: str
    resolved_intent: ReaderAskResolvedIntent | None = None
    citations: list[ReaderAskCitation] = Field(default_factory=list)
    action_proposals: list[ReaderAskActionProposal] = Field(default_factory=list)
    tool_trace: list[ReaderAskToolTraceEntry] = Field(default_factory=list)
    response_cards: list[ReaderAskResponseCard] = Field(default_factory=list)
    usage_summary: dict[str, Any] | None = None
    billed_points: int = 0
    resolved_context: ReaderAskResolvedContextSummary
    context_plan: ReaderAskContextPlan | None = None
    resolved_context_input: ReaderAskResolvedContextInput | None = None
    run_info: ReaderAskRunInfo | None = None
    supplement_candidates: list[ReaderAskSupplementCandidate] = Field(default_factory=list)


class ReaderAskStreamEnvelope(BaseModel):
    event: str
    data: dict[str, Any]
