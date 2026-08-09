from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.user_assets.favorites import FavoriteTargetType
from app.schemas.user_editorial_assets import UserEditorialAssetAnchor

ReaderAskAnchorType = Literal[
    "sentence",
    "text_range",
    "multi_text",
    "sentence_entry",
    "user_annotation",
    "reader_note",
    "dictionary_entry",
]
ReaderAskMessageRole = Literal["user", "assistant", "system"]
ReaderAskMessageStatus = Literal["pending", "streaming", "completed", "failed", "interrupted"]
ReaderAskCitationKind = Literal[
    "anchor",
    "vocabulary",
    "dictionary_entry",
    "dictionary_ai",
]
ReaderAskActionType = Literal[
    "save_note",
    "save_highlight",
    "create_supplement_grammar_note",
]
ReaderAskActionStatus = Literal["pending", "executing", "confirmed", "executed", "rejected"]
ReaderAskToolStatus = Literal["started", "completed", "failed"]
ReaderAskTaskMode = Literal["explain", "breakdown", "vocabulary", "grammar", "practice", "general"]
ReaderAskResolvedIntent = ReaderAskTaskMode
ReaderAskReferenceResolutionStatus = Literal["not_needed", "resolved", "ambiguous", "not_found"]
ReaderAskEntryAction = Literal[
    "ask_about_this",
    "explain_this",
    "why_here",
    "lookup_in_context",
]
ReaderAskAttachmentKind = Literal[
    "text_selection",
    "annotation_ref",
    "analysis_ref",
    "supplement_ref",
    "record_ref",
]
ReaderAskResponseCardType = Literal[
    "grammar_note_card",
    "sentence_breakdown_card",
]
ReaderAskSubmissionMode = Literal["chat", "quick_action"]
ReaderAskSupplementType = Literal["grammar_note"]
ReaderAskSupplementLifecycleStatus = Literal["candidate", "persisted", "deleted"]
ReaderAskWorkingSetMode = Literal[
    "anchor_local",
    "article_overview",
    "explicit_external_record",
    "known_reference",
    "clarification",
]
ReaderAskPlannerAssetType = Literal["analysis", "supplement"]
ReaderAskContextScope = Literal["sentence", "paragraph", "article", "cross_article"]
ReaderAskAnswerPolicy = Literal["concise", "detailed", "step_by_step", "comparative"]


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
            if any(
                value is None or (isinstance(value, str) and not value.strip())
                for value in required
            ):
                raise ValueError(
                    "text_range anchors require sentence_id, selected_text, and text_hash"
                )
        if self.anchor_type == "dictionary_entry" and self.dict_entry_id is None and not self.query:
            raise ValueError("dictionary_entry anchors require dict_entry_id or query")
        return self


class ReaderAskReadingRecordAnchor(UserEditorialAssetAnchor):
    """D6-A3 Ask write-proposal anchor.

    This intentionally inherits the UserEditorialAssetAnchor contract so Ask
    proposals can carry the new Reading Record anchor payload without wiring it
    into DB writes yet.
    """


ReaderAskWriteProposalAnchor = Annotated[
    ReaderAskReadingRecordAnchor | ReaderAskAnchorRef,
    Field(union_mode="left_to_right"),
]


class ReaderAskWriteProposalPayload(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    record_id: str | None = None
    anchor: ReaderAskWriteProposalAnchor | None = None
    note_text: str | None = None
    target_key: str | None = None
    target_sentence_id: str | None = None

    @model_validator(mode="after")
    def validate_anchor_or_legacy_target(self) -> ReaderAskWriteProposalPayload:
        if self.anchor is None and not (self.target_key or self.target_sentence_id):
            raise ValueError(
                "save_note/save_highlight proposals require anchor or legacy target fields"
            )
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
    has_reader_notes: bool = False


class ReaderAskCurrentRecordAffordances(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = None
    available_context_capabilities: list[str] = Field(default_factory=list)
    has_article_overview: bool = False
    has_sentence_entries: bool = False
    has_annotations: bool = False
    has_reader_notes: bool = False


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
    def validate_payload(self) -> ReaderAskAttachmentPayload:
        if self.anchor_type == "multi_text" and len(self.segments) < 2:
            raise ValueError("multi_text payloads require at least two segments")
        if self.anchor_type == "text_range":
            required = [
                self.sentence_id,
                self.selected_text,
                self.text_hash,
                self.start_offset,
                self.end_offset,
            ]
            if any(
                value is None or (isinstance(value, str) and not value.strip())
                for value in required
            ):
                raise ValueError(
                    "text_range payloads require sentence_id, selected_text, offsets, and text_hash"
                )
        return self


class ReaderAskAttachmentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_surface: str
    entry_action: ReaderAskEntryAction | None = None
    record_id: str | None = None
    record_title: str | None = None
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
    result_json: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> ReaderAskActionProposal:
        if self.action_type in ("save_note", "save_highlight"):
            ReaderAskWriteProposalPayload.model_validate(self.payload_json)
        return self


class ReaderAskToolTraceEntry(BaseModel):
    tool_name: str
    status: ReaderAskToolStatus
    started_at: str | None = None
    completed_at: str | None = None
    input_summary: str | None = None
    summary: str | None = None
    next_actions: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ReaderAskResolvedContextSummary(BaseModel):
    record_id: str
    record_title: str | None = None
    anchor_count: int = 0
    explicit_attachment_count: int = 0
    used_cross_record_context: bool = False
    current_sentence_used: bool = False
    current_paragraph_used: bool = False
    used_record_insights: bool = False
    used_dictionary: bool = False
    source_labels: list[str] = Field(default_factory=list)


class ReaderAskContextPlan(BaseModel):
    entry_action: ReaderAskEntryAction
    explicit_attachment_count: int = 0
    normalized_anchor_count: int = 0
    primary_anchor_type: ReaderAskAnchorType | None = None
    reference_query: str | None = None
    reference_resolution_attempted: bool = False
    reference_resolution_status: ReaderAskReferenceResolutionStatus = "not_needed"
    reference_resolution_reason: str | None = None
    expanded_record_ids: list[str] = Field(default_factory=list)
    used_cross_record_context: bool = False
    cross_record_context_reason: str | None = None
    used_record_context: bool = False
    record_context_reason: str | None = None
    used_record_insights: bool = False
    record_insights_reason: str | None = None
    used_article_overview: bool = False
    article_overview_reason: str | None = None
    used_dictionary: bool = False
    dictionary_reason: str | None = None
    external_record_context_reason: str | None = None
    structured_asset_lookup_reason: str | None = None
    external_asset_selection_reason: str | None = None
    clarification_reason: str | None = None
    source_labels: list[str] = Field(default_factory=list)


class ReaderAskCurrentRecordContext(BaseModel):
    record_id: str
    record_title: str | None = None
    local_context: dict[str, Any] | None = None
    record_insights: list[dict[str, Any]] = Field(default_factory=list)
    article_overview: str | None = None
    article_overview_status: str | None = None
    article_overview_source: str | None = None
    article_overview_confidence: str | None = None
    source_labels: list[str] = Field(default_factory=list)


class ReaderAskExternalRecordContext(BaseModel):
    record_id: str
    record_title: str | None = None
    article_overview: str | None = None
    article_overview_status: str | None = None
    article_overview_source: str | None = None
    article_overview_confidence: str | None = None
    record_insights: list[str] = Field(default_factory=list)
    source_labels: list[str] = Field(default_factory=list)
    reason: str | None = None


class ReaderAskExternalAssetContext(BaseModel):
    record_id: str
    record_title: str | None = None
    asset_type: Literal["analysis", "supplement"]
    asset_id: str
    entry_type: str | None = None
    asset_title: str | None = None
    content_md: str | None = None
    content_summary: str | None = None
    source_labels: list[str] = Field(default_factory=list)
    reason: str | None = None


class ReaderAskResolvedContextInput(BaseModel):
    page_identity: ReaderAskPageIdentity
    entry_action: ReaderAskEntryAction
    attachments: list[ReaderAskAttachment] = Field(default_factory=list)
    normalized_anchors: list[ReaderAskAnchorRef] = Field(default_factory=list)
    current_record_context: ReaderAskCurrentRecordContext | None = None
    external_record_contexts: list[ReaderAskExternalRecordContext] = Field(default_factory=list)
    external_asset_contexts: list[ReaderAskExternalAssetContext] = Field(default_factory=list)


class ReaderAskPlannerHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant", "system"]
    content_md: str
    resolved_intent: ReaderAskResolvedIntent | None = None


class ReaderAskPlannerReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    requested: bool = False
    query: str | None = None
    reason: str | None = None


class ReaderAskPlannerStructuredAssetRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    requested: bool = False
    requested_asset_type: ReaderAskPlannerAssetType | None = None
    reason: str | None = None

    @field_validator("requested_asset_type", mode="before")
    @classmethod
    def normalize_requested_asset_type(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        return _ASSET_TYPE_ALIASES.get(normalized, value)


class ReaderAskPlannerWorkingSetDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    local_context_window_needed: bool = False
    record_insights_needed: bool = False
    article_overview_needed: bool = False
    dictionary_needed: bool = False
    cross_record_context_allowed: bool = False
    external_asset_lookup_needed: bool = False


class ReaderAskPlannerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_message: str
    entry_action: ReaderAskEntryAction
    page_identity: ReaderAskPageIdentity
    current_record_affordances: ReaderAskCurrentRecordAffordances
    attachments: list[ReaderAskAttachment] = Field(default_factory=list)
    normalized_anchors: list[ReaderAskAnchorRef] = Field(default_factory=list)
    history: list[ReaderAskPlannerHistoryMessage] = Field(default_factory=list)


_INTENT_ALIASES: dict[str, ReaderAskResolvedIntent] = {
    "explain": "explain",
    "解释": "explain",
    "讲解": "explain",
    "explanation": "explain",
    "breakdown": "breakdown",
    "拆句": "breakdown",
    "拆解": "breakdown",
    "语法拆解": "breakdown",
    "vocabulary": "vocabulary",
    "词义": "vocabulary",
    "词汇": "vocabulary",
    "单词": "vocabulary",
    "grammar": "grammar",
    "语法": "grammar",
    "syntax": "grammar",
    "practice": "practice",
    "练习": "practice",
    "exercise": "practice",
    "general": "general",
    "总结": "general",
    "概括": "general",
    "summarize": "general",
    "summary": "general",
    "翻译": "general",
    "translate": "general",
    "对比": "general",
    "比较": "general",
    "compare": "general",
    "分析": "general",
    "analyze": "general",
    "复习": "general",
    "review": "general",
}

_ASSET_TYPE_ALIASES: dict[str, ReaderAskPlannerAssetType] = {
    "analysis": "analysis",
    "解析": "analysis",
    "分析": "analysis",
    "sentence_analysis": "analysis",
    "supplement": "supplement",
    "补充": "supplement",
    "旁注": "supplement",
    "grammar_note": "supplement",
}


ReaderAskClarificationMode = Literal["none", "must_clarify", "can_answer_with_followup"]


class ReaderAskPlannerDecision(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    resolved_intent: ReaderAskResolvedIntent
    intent_label: str | None = None
    clarification_only: bool = False
    clarification_mode: ReaderAskClarificationMode = "none"
    clarification_reason: str | None = None
    reference_request: ReaderAskPlannerReferenceRequest = Field(
        default_factory=ReaderAskPlannerReferenceRequest
    )
    structured_asset_request: ReaderAskPlannerStructuredAssetRequest = Field(
        default_factory=ReaderAskPlannerStructuredAssetRequest
    )
    working_set: ReaderAskPlannerWorkingSetDecision = Field(
        default_factory=ReaderAskPlannerWorkingSetDecision
    )
    rationale: str | None = None
    context_scope: ReaderAskContextScope | None = None
    decision_confidence: Literal["high", "medium", "low"] | None = None
    requires_local_anchor: bool | None = None
    answer_policy: ReaderAskAnswerPolicy | None = None
    tool_hints: list[str] | None = None

    @field_validator("resolved_intent", mode="before")
    @classmethod
    def normalize_resolved_intent(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        return _INTENT_ALIASES.get(normalized, value)

    @model_validator(mode="after")
    def sync_clarification_fields(self) -> ReaderAskPlannerDecision:
        # clarification_mode is the source of truth; sync clarification_only from it
        if self.clarification_mode == "must_clarify":
            self.clarification_only = True
        elif self.clarification_mode == "can_answer_with_followup":
            self.clarification_only = False
        # Backward compat: if clarification_only=True but clarification_mode="none",
        # upgrade clarification_mode to "must_clarify"
        elif self.clarification_only and self.clarification_mode == "none":
            self.clarification_mode = "must_clarify"  # type: ignore[assignment]
        return self


class ReaderAskRunInfo(BaseModel):
    turn_id: str
    run_id: str
    run_attempt: int = 1
    supersedes_run_id: str | None = None


class ReaderAskDisambiguationCandidate(BaseModel):
    record_id: str
    title: str | None = None
    updated_at: str | None = None
    overview_hint: str | None = None


class ReaderAskDisambiguation(BaseModel):
    required: bool = False
    reason: str | None = None
    query: str | None = None
    selection_mode: Literal["panel_cards"] = "panel_cards"
    candidates: list[ReaderAskDisambiguationCandidate] = Field(default_factory=list)


class ReaderAskAssetDisambiguationCandidate(BaseModel):
    asset_type: Literal["analysis", "supplement"]
    asset_id: str
    entry_type: str | None = None
    title: str | None = None
    summary: str | None = None


class ReaderAskAssetDisambiguation(BaseModel):
    required: bool = False
    reason: str | None = None
    record_id: str | None = None
    record_title: str | None = None
    candidates: list[ReaderAskAssetDisambiguationCandidate] = Field(default_factory=list)


class ReaderAskTraceSummary(BaseModel):
    planner_mode: Literal[
        "direct_answer",
        "needs_local_clarification",
        "partial_answer_with_followup",
        "known_reference_resolved",
        "known_reference_ambiguous",
        "known_reference_not_found",
    ] = "direct_answer"
    reference_resolution_status: ReaderAskReferenceResolutionStatus = "not_needed"
    working_set_mode: ReaderAskWorkingSetMode = "anchor_local"
    used_known_reference_resolution: bool = False
    used_external_record_context: bool = False
    used_structured_asset_lookup: bool = False
    used_hitp_disambiguation: bool = False
    used_external_asset_context: bool = False
    used_external_asset_disambiguation: bool = False
    supplement_generation_used: bool = False
    supplement_persisted_count: int = 0
    supplement_deleted_count: int = 0
    cross_record_context_allowed: bool = False
    cross_record_context_used: bool = False
    tool_steps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ReaderAskContextRecordItem(BaseModel):
    record_id: str
    title: str | None = None
    updated_at: str | None = None
    overview_hint: str | None = None
    overview_hint_status: str | None = None
    overview_hint_source: str | None = None


class ReaderAskContextRecordSearchResponse(BaseModel):
    items: list[ReaderAskContextRecordItem] = Field(default_factory=list)


class ReaderAskSupplementCandidate(BaseModel):
    candidate_id: str
    supplement_type: ReaderAskSupplementType
    lifecycle_status: Literal["candidate"] = "candidate"
    target_key: str
    sentence_id: str
    paragraph_id: str | None = None
    title: str
    content: str
    anchor: ReaderAskAnchorRef | ReaderAskReadingRecordAnchor
    schema_version: str
    created_from_turn_run_id: str
    label: str = "AI 补充语法旁注"


class ReaderAskPersistedSupplement(BaseModel):
    supplement_id: str
    supplement_type: ReaderAskSupplementType
    lifecycle_status: Literal["persisted", "deleted"] = "persisted"
    record_id: str
    record_title: str | None = None
    target_key: str | None = None
    sentence_id: str | None = None
    paragraph_id: str | None = None
    title: str
    content: str
    source_kind: Literal["assistant_supplement"] = "assistant_supplement"
    schema_version: str
    created_from_turn_run_id: str
    created_at: str | None = None


class ReaderAskSentenceBreakdownPart(BaseModel):
    label: str
    text: str
    note: str | None = None


class ReaderAskGrammarNoteCardSpan(BaseModel):
    text: str
    role: str | None = None


class ReaderAskGrammarNoteCard(BaseModel):
    card_type: Literal["grammar_note_card"] = "grammar_note_card"
    sentence_text: str
    focus_text: str
    label: str
    note_zh: str
    spans: list[ReaderAskGrammarNoteCardSpan] = Field(default_factory=list)
    analysis_scope: Literal["focus_span", "full_sentence"]
    origin: Literal["ask_ai"] = "ask_ai"


class ReaderAskSentenceBreakdownCard(BaseModel):
    card_type: Literal["sentence_breakdown_card"] = "sentence_breakdown_card"
    sentence_text: str
    translation_zh: str | None = None
    main_clause: str | None = None
    analysis_zh: str | None = None
    parts: list[ReaderAskSentenceBreakdownPart] = Field(default_factory=list)
    origin: Literal["ask_ai"] = "ask_ai"


ReaderAskResponseCard = Annotated[
    ReaderAskGrammarNoteCard | ReaderAskSentenceBreakdownCard,
    Field(discriminator="card_type"),
]


class ReaderAskSelectedModel(BaseModel):
    key: str
    label: str
    description: str | None = None
    model_name: str | None = None
    replan_model_name: str | None = None
    price_multiplier: float = 1.0
    # ASK-WEB-G1-R2: server-declared Web Search capability for this model
    # option. ``"available"`` only when a real provider is wired via
    # the current ResolvedModelConfig binding — never inferred
    # from the request toggle or scope. The frontend gates Search toggle
    # visibility/enablement on this signal (in addition to the page
    # scope), so the user cannot request a capability the host has not
    # declared. Default ``"unavailable"`` is fail-closed for legacy
    # clients that do not populate the field.
    web_search_capability: Literal["unavailable", "available"] = "unavailable"


class ReaderAskModelOptionSummary(ReaderAskSelectedModel):
    is_default: bool = False


class ReaderAskModelOptionListResponse(BaseModel):
    default_key: str
    items: list[ReaderAskModelOptionSummary]


class ReaderAskThreadSummary(BaseModel):
    id: str
    record_id: str
    title: str | None = None
    is_default: bool
    selected_model: ReaderAskSelectedModel | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str
    last_message_at: str | None = None


class ReaderAskThreadListResponse(BaseModel):
    items: list[ReaderAskThreadSummary]


class ReaderAskActionConfirmResult(BaseModel):
    annotation_id: str | None = None
    annotation_type: str | None = None
    note_id: str | None = None
    target_key: str | None = None
    record_id: str | None = None
    supplement_projection: dict[str, Any] | None = None
    persisted_supplement: ReaderAskPersistedSupplement | None = None


class ReaderAskActionConfirmRequest(BaseModel):
    confirmed: bool = True


class ReaderAskActionConfirmResponse(BaseModel):
    ok: bool
    action_id: str
    status: ReaderAskActionStatus
    result: ReaderAskActionConfirmResult = Field(default_factory=ReaderAskActionConfirmResult)


class ReaderAskDeleteSupplementResponse(BaseModel):
    deleted: bool = True
    supplement_id: str
    record_id: str
    target_key: str | None = None
    lifecycle_status: Literal["deleted"] = "deleted"
    persisted_supplement: ReaderAskPersistedSupplement | None = None


class ReaderAskMessageRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Optional model key for thread option resolution compatibility only.
    # ASK-RETRY-CONTRACT-R3: must NOT invent a new lane or override the
    # original turn's capability snapshot. Lane is always the persisted
    # execution_version of the target assistant message.
    model: str | None = None


class ReaderAskSubmissionPublicMessage(BaseModel):
    """Safe public projection of a message for reconcile hydrate (R5)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    thread_id: str
    role: ReaderAskMessageRole
    status: ReaderAskMessageStatus
    content_md: str
    citations: list[ReaderAskCitation] = Field(default_factory=list)
    agentic_citations: list[Any] | None = None
    agentic_answer_blocks: list[Any] | None = None
    agentic_web_search: Any | None = None
    execution_version: Literal["reader_record_ask_agentic_v2"] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ReaderAskSubmissionReconcileResponse(BaseModel):
    """ASK-RETRY-CONTRACT-R5 — typed reconciliation with optional hydrate."""

    model_config = ConfigDict(extra="forbid")

    client_submission_id: str
    thread_id: str
    status: Literal[
        "claimed",
        "streaming",
        "completed",
        "failed",
        "cancelled",
        "not_found",
    ]
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    terminal_code: str | None = None
    claim_generation: int | None = None
    action_hint: Literal["resend", "retry", "reask", "wait", "none"] | None = None
    # Full public messages when terminal / available — never internal handles.
    user_message: ReaderAskSubmissionPublicMessage | None = None
    assistant_message: ReaderAskSubmissionPublicMessage | None = None


class ReaderAskFollowUpSuggestion(BaseModel):
    """A single follow-up prompt chip (Round 2 suggest_prompts tool).

    ``label`` is the chip text (≤40 chars); ``prompt`` is the actual
    user message to send when the chip is clicked (≤200 chars).
    """

    label: str
    prompt: str


class ReaderRecordAskMessageRequest(BaseModel):
    """D6-A6: new Reading Record Ask message request contract.

    This is the canonical Reading Record Ask v2 request contract.
    It accepts Reading Record anchors only.

    ASK-UX-COT-COMPOSER-R3 P2 — plural focus anchors:
    - ``focus_anchors`` is the canonical multi-selection field (≤4: one
      auto-ingested selection plus up to three user-pinned selections). New
      Web clients send every auto/manual selection anchor here.
    - ``anchor`` (singular) is retained ONLY as the legacy compatibility
      entry for old single-selection callers. When ``focus_anchors`` is
      present it wins; the singular field is never silently merged in
      addition. Each provided anchor is independently gate-validated
      (record/base/generation/document) and ANY invalid, unauthorized,
      or stale anchor fails the whole request closed — never partially
      accepted before the model call.
    - Reader-only fields never appear on the generic analysis request.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=5000)
    entry_action: ReaderAskEntryAction = "ask_about_this"
    anchor: ReaderAskReadingRecordAnchor | None = None
    focus_anchors: list[ReaderAskReadingRecordAnchor] | None = Field(
        default=None, max_length=4
    )
    model: str | None = None
    # User-visible Web Search authorization (G1-R1). ``allowed`` only grants
    # turn capability; it never forces a search. The agent decides whether
    # to invoke ``search_web``; Retry replays the original turn's mode
    # instead of re-reading current UI state.
    web_search_mode: Literal["disabled", "allowed"] = "disabled"
    # ASK-RETRY-CONTRACT-R2: client-generated UUID for idempotent claim.
    # Same value re-submitted after a network blip must not create a
    # second user/assistant pair or re-call the model.
    client_submission_id: UUID | None = None


class ReaderRecordAskActionConfirmRequest(BaseModel):
    """D6-A6: confirm request for a Reading Record Ask action proposal."""

    model_config = ConfigDict(extra="forbid")

    confirmed: bool = True


class ReaderRecordAskPendingResponse(BaseModel):
    """D6-A6: stable pending/disabled response for the new Reading Record Ask path.

    The route returns HTTP 409 with this body while the execution / write
    path remains disabled.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["pending"] = "pending"
    code: str
    message: str
    reading_record_id: str
    action_id: str | None = None


class ReaderAskStreamEnvelope(BaseModel):
    event: str
    data: dict[str, Any]


class ReaderAskPlanningSnapshotRecord(BaseModel):
    resolved_intent: ReaderAskResolvedIntent
    planner_decision: dict[str, Any] = Field(default_factory=dict)
    planner_validation_status: str = "not_run"
    reference_needs: dict[str, Any] = Field(default_factory=dict)
    retrieval_needs: str
    resolved_references: dict[str, Any] = Field(default_factory=dict)
    structured_asset_needs: dict[str, Any] = Field(default_factory=dict)
    structured_asset_resolution: dict[str, Any] = Field(default_factory=dict)
    working_set: dict[str, Any] = Field(default_factory=dict)
    context_plan: dict[str, Any] = Field(default_factory=dict)
    trace_summary: dict[str, Any] = Field(default_factory=dict)


class ReaderAskTurnRunRecord(BaseModel):
    id: str
    message_id: str
    thread_id: str
    user_id: str
    record_id: str
    turn_id: str
    run_attempt: int
    supersedes_run_id: str | None = None
    status: Literal["streaming", "completed", "failed", "interrupted"]
    resolved_intent: ReaderAskResolvedIntent | None = None
    user_visible_output_json: dict[str, Any] | None = None
    usage_summary_json: dict[str, Any] | None = None
    usage_event_id: str | None = None
    started_at: str
    completed_at: str | None = None
    failed_at: str | None = None
    created_at: str
    updated_at: str


class ReaderAskEvalTraceRecord(BaseModel):
    turn_run_id: str
    trace_schema_version: str
    planning_snapshot_json: dict[str, Any] = Field(default_factory=dict)
    capability_trace_json: dict[str, Any] = Field(default_factory=dict)
    action_audit_json: list[dict[str, Any]] = Field(default_factory=list)
    supplement_audit_json: list[dict[str, Any]] = Field(default_factory=list)
    metrics_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
