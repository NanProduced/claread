from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.annotation import (
    TEXT_RANGE_HASH_ALGORITHM,
    TEXT_RANGE_OFFSET_UNIT,
    compute_text_range_hash,
    utf16_code_unit_length,
)
from app.schemas.reader_documents import CandidateReadingDocumentStatus
from app.schemas.reader_input_adapter import (
    InputAdapterSourceType,
    SourceArtifactKind,
    SourceArtifactStatus,
    SourceArtifactStorageProvider,
    InputSuitabilityResult,
)

ReadingRecordLifecycleStatus = Literal["active", "cancelled", "superseded", "deleted"]
ReadingRecordProductState = Literal[
    "processing",
    "needs_confirmation",
    "readable_enhancing",
    "action_required",
    "failed",
    "deleted",
]
ReadingRecordReadinessState = Literal[
    "submitted",
    "candidate_base_ready",
    "article_ready",
    "initial_enhancement_ready",
    "coverage_complete",
]
ReaderRunStatus = Literal[
    "queued",
    "running",
    "waiting_user",
    "waiting_quota",
    "paused",
    "completed",
    "failed_retryable",
    "failed_terminal",
    "cancelled",
    "superseded",
]
ReaderJobStatus = Literal[
    "queued",
    "claimed",
    "retry_later",
    "paused",
    "skipped",
    "succeeded",
    "failed_terminal",
    "cancelled",
    "superseded",
]
ReaderLayerType = Literal["translation", "vocabulary", "grammar_note", "sentence_analysis"]
ReaderTitleGenerationStatus = Literal["pending", "succeeded", "failed_retryable"]
ReaderEnhancementProgressOverallStatus = Literal[
    "processing",
    "readable_enhancing",
    "ready",
    "failed",
    "action_required",
]
ReaderEnhancementProgressLayerStatus = Literal[
    "not_started",
    "queued",
    "processing",
    "succeeded",
    "failed",
    "action_required",
]
ReaderEnhancementCapability = Literal["translation", "vocabulary", "grammar"]
VocabularyItemType = Literal["vocab_highlight", "phrase_gloss", "context_gloss"]
VocabularyPhraseType = Literal[
    "collocation",
    "phrasal_verb",
    "idiom",
    "proper_noun",
    "compound",
    "other",
]
ParsedDecisionState = Literal["not_started", "partial", "parsed", "skipped", "failed"]
AnchorSegmentType = Literal["sentence", "clause", "fallback_window"]
ReaderBoundaryQuality = Literal["normal", "low"]
ReaderUnitType = Literal["body", "heading", "list", "quote", "unknown", "fallback"]
ReaderLayerTargetScope = Literal["unit", "anchor_segment", "unit_range", "record"]
ReaderArtifactInputSourceType = Literal["file", "pdf", "image"]
ReaderArtifactOriginalInputType = Literal["file_ref", "image_ref"]

# ---------------------------------------------------------------------------#
# Reader Orchestration reading strategy contract (T1 backend contract restore)
#
# `reading_goal` / `reading_variant` are first-class facts on `reading_records`.
# They are the truth owner for Reader strategy in the new orchestration. They
# MUST NOT be inferred from `source_metadata`.
#
# Scope: only `daily_reading` and `exam` are wired into the new Reader
# Orchestration. `academic` / `academic_general` from legacy AI Workflow are
# intentionally excluded; submitting them must fail closed at the schema layer
# rather than being silently mapped to a daily/exam variant.
#
# The centralized defaults below backfill historical records (DB migration) and
# keep the existing Web BFF submit body working when the client omits the
# fields. Defaults are persisted as first-class facts; they are not a fallback
# that lives in worker code or in `source_metadata`.
# ---------------------------------------------------------------------------#
ReaderOrchestrationReadingGoal = Literal["daily_reading", "exam"]
ReaderOrchestrationReadingVariant = Literal[
    "beginner_reading",
    "intermediate_reading",
    "intensive_reading",
    "gaokao",
    "cet",
    "kaoyan",
    "tem",
    "ielts_toefl",
]

READER_ORCHESTRATION_GOAL_VARIANT_MAP: dict[
    ReaderOrchestrationReadingGoal, frozenset[ReaderOrchestrationReadingVariant]
] = {
    "daily_reading": frozenset(
        {"beginner_reading", "intermediate_reading", "intensive_reading"}
    ),
    "exam": frozenset({"gaokao", "cet", "kaoyan", "tem", "ielts_toefl"}),
}

# Centralized historical/missing-strategy defaults. Used by the DB migration
# to backfill existing rows, by submit schemas as field defaults, and by the
# snapshot model so legacy fixtures remain valid. Do not duplicate these in
# workers or in source_metadata.
DEFAULT_READER_ORCHESTRATION_READING_GOAL: ReaderOrchestrationReadingGoal = (
    "daily_reading"
)
DEFAULT_READER_ORCHESTRATION_READING_VARIANT: ReaderOrchestrationReadingVariant = (
    "intermediate_reading"
)


def _validate_reader_orchestration_strategy(
    reading_goal: ReaderOrchestrationReadingGoal,
    reading_variant: ReaderOrchestrationReadingVariant,
) -> None:
    """Fail-closed validator for the new Reader Orchestration strategy pair.

    `academic` / `academic_general` are rejected by the Literal types above.
    This helper additionally enforces that `reading_variant` belongs to
    `reading_goal`. It is the single chokepoint for variant-in-goal checks;
    repository / worker code should not re-implement this mapping.
    """
    allowed_variants = READER_ORCHESTRATION_GOAL_VARIANT_MAP.get(reading_goal)
    if allowed_variants is None or reading_variant not in allowed_variants:
        raise ValueError(
            f"reading_variant={reading_variant!r} does not belong to "
            f"reading_goal={reading_goal!r} in the new Reader Orchestration scope"
        )


# `reading_goal` / `reading_variant` are reserved at the top level of
# `source_metadata`. Allowing them there would create a second truth source
# (next to the first-class `reading_records` columns) and let a client split
# record truth from Ask strategy. Nested keys inside sub-objects are not
# affected. Apply this set via `_reject_reserved_strategy_keys_in_source_metadata`
# on every Reader Orchestration submit schema.
READER_ORCHESTRATION_RESERVED_SOURCE_METADATA_KEYS: frozenset[str] = frozenset(
    {"reading_goal", "reading_variant"}
)


def _reject_reserved_strategy_keys_in_source_metadata(
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Reject top-level reserved strategy keys on submit `source_metadata`.

    Returns the metadata unchanged when no reserved key is present (or when the
    value is `None`). Raises `ValueError` so Pydantic surfaces it as a 422 at
    the API boundary.
    """
    if metadata is None:
        return None
    conflicting = READER_ORCHESTRATION_RESERVED_SOURCE_METADATA_KEYS & metadata.keys()
    if conflicting:
        raise ValueError(
            "source_metadata must not carry reserved strategy keys at the "
            f"top level: {sorted(conflicting)}. Use the first-class "
            "reading_goal / reading_variant request fields instead."
        )
    return metadata


class ReaderUnitAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_type: Literal["unit"] = "unit"
    base_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")
    hash_algorithm: Literal["fnv1a32-utf16"] = TEXT_RANGE_HASH_ALGORITHM


class ReaderTextRangeAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_type: Literal["text_range"] = "text_range"
    base_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    anchor_segment_id: str = Field(min_length=1)
    sentence_id: str | None = None
    segment_type: AnchorSegmentType = "sentence"
    offset_unit: Literal["utf16"] = TEXT_RANGE_OFFSET_UNIT
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    selected_text: str = Field(min_length=1)
    text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")
    hash_algorithm: Literal["fnv1a32-utf16"] = TEXT_RANGE_HASH_ALGORITHM

    @model_validator(mode="after")
    def validate_offsets(self) -> ReaderTextRangeAnchor:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        if utf16_code_unit_length(self.selected_text) != self.end_offset - self.start_offset:
            raise ValueError("selected_text UTF-16 length must match offset span")
        if compute_text_range_hash(self.selected_text) != self.text_hash:
            raise ValueError("text_hash must match selected_text")
        return self


class TranslationGenerationGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_segment_ids: list[str] = Field(min_length=1)
    translated_text: str = Field(min_length=1)


class TranslationLayerGenerationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[TranslationGenerationGroup] = Field(min_length=1)


class TranslationGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1)
    anchor_segment_ids: list[str] = Field(min_length=1)
    source_text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")
    translated_text: str = Field(min_length=1)


class TranslationLayerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[TranslationGroup] = Field(min_length=1)


class VocabularyHighlightItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["vocab_highlight"] = "vocab_highlight"
    anchor: ReaderTextRangeAnchor
    headword: str = Field(min_length=1)
    brief_explanation: str | None = None
    reason: str | None = None

    @field_validator("headword")
    @classmethod
    def validate_headword(cls, value: str) -> str:
        if any(char.isspace() for char in value):
            raise ValueError("headword must be a single token without spaces")
        return value


class VocabularyPhraseGlossItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["phrase_gloss"] = "phrase_gloss"
    anchor: ReaderTextRangeAnchor
    phrase: str = Field(min_length=1)
    phrase_type: VocabularyPhraseType
    gloss: str = Field(min_length=1)
    example: str | None = None


class VocabularyContextGlossItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["context_gloss"] = "context_gloss"
    anchor: ReaderTextRangeAnchor
    display: str = Field(min_length=1)
    gloss: str = Field(min_length=1)
    reason: str = Field(min_length=1)


VocabularyLayerItem = Annotated[
    VocabularyHighlightItem | VocabularyPhraseGlossItem | VocabularyContextGlossItem,
    Field(discriminator="item_type"),
]


class VocabularyLayerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    items: list[VocabularyLayerItem] = Field(default_factory=list)


class GrammarNoteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["grammar_note"] = "grammar_note"
    spans: list[ReaderTextRangeAnchor] = Field(min_length=1, max_length=4)
    grammar_point: str = Field(min_length=1)
    pattern: str | None = None
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_same_unit_spans(self) -> GrammarNoteItem:
        base_ids = {span.base_id for span in self.spans}
        unit_ids = {span.unit_id for span in self.spans}
        if len(base_ids) != 1:
            raise ValueError("grammar_note spans must belong to the same base_id")
        if len(unit_ids) != 1:
            raise ValueError("grammar_note spans must belong to the same unit_id")
        return self


class SentenceAnalysisChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    label: str = Field(min_length=1)
    text: str = Field(min_length=1)


class SentenceAnalysisItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["sentence_analysis"] = "sentence_analysis"
    anchor: ReaderTextRangeAnchor
    label: str = Field(min_length=1)
    analysis: str = Field(min_length=1)
    chunks: list[SentenceAnalysisChunk] = Field(min_length=1)


class GrammarNoteLayerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    items: list[GrammarNoteItem] = Field(min_length=1)


class SentenceAnalysisLayerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    items: list[SentenceAnalysisItem] = Field(min_length=1)


class GrammarBundleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    grammar_notes: list[GrammarNoteItem] = Field(default_factory=list)
    sentence_analyses: list[SentenceAnalysisItem] = Field(default_factory=list)


class ReaderSnapshotBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonicalizer_version: str = Field(min_length=1)
    builder_version: str = Field(min_length=1)
    segmenter_version: str = Field(min_length=1)
    text_length_utf16: int = Field(ge=1)
    hash_algorithm: Literal["fnv1a32-utf16"] = TEXT_RANGE_HASH_ALGORITHM


class ReaderSnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    display_title_zh: str | None = Field(default=None, min_length=1)
    title_generation_status: ReaderTitleGenerationStatus = "pending"
    title_generation_error_code: str | None = Field(default=None, min_length=1)
    title_generation_error_message: str | None = Field(
        default=None,
        min_length=1,
        max_length=240,
    )
    created_at: datetime
    source_type: str = Field(min_length=1)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    generation: int = Field(ge=1)
    product_state: ReadingRecordProductState
    readiness_state: ReadingRecordReadinessState
    # Reader strategy first-class facts. Persisted on `reading_records` and
    # exposed on every snapshot. Defaults keep legacy fixtures valid; the
    # repository always passes the DB-loaded values for real records.
    reading_goal: ReaderOrchestrationReadingGoal = Field(
        default=DEFAULT_READER_ORCHESTRATION_READING_GOAL
    )
    reading_variant: ReaderOrchestrationReadingVariant = Field(
        default=DEFAULT_READER_ORCHESTRATION_READING_VARIANT
    )

    @model_validator(mode="after")
    def _validate_reader_strategy_pair(self) -> ReaderSnapshotRecord:
        _validate_reader_orchestration_strategy(
            reading_goal=self.reading_goal,
            reading_variant=self.reading_variant,
        )
        return self


class ReaderSnapshotNavigationUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    order_index: int = Field(ge=1)
    unit_type: ReaderUnitType
    boundary_quality: ReaderBoundaryQuality = "normal"
    label: str | None = None
    base_start_utf16: int = Field(ge=0)
    base_end_utf16: int = Field(gt=0)
    text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")
    hash_algorithm: Literal["fnv1a32-utf16"] = TEXT_RANGE_HASH_ALGORITHM


class ReaderSnapshotNavigation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[ReaderSnapshotNavigationUnit] = Field(default_factory=list)


class ReaderSnapshotAnchorSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_segment_id: str = Field(min_length=1)
    sentence_id: str = Field(min_length=1)
    paragraph_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    order_index: int = Field(ge=1)
    unit_order_index: int = Field(ge=1)
    segment_type: AnchorSegmentType
    boundary_quality: ReaderBoundaryQuality = "normal"
    base_start_utf16: int = Field(ge=0)
    base_end_utf16: int = Field(gt=0)
    unit_start_utf16: int = Field(ge=0)
    unit_end_utf16: int = Field(gt=0)
    text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")
    hash_algorithm: Literal["fnv1a32-utf16"] = TEXT_RANGE_HASH_ALGORITHM


class ReaderSnapshotLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1)
    layer_type: str = Field(min_length=1)
    layer_subtype: str | None = None
    owner: Literal["system_ai"] = "system_ai"
    base_id: str = Field(min_length=1)
    target_scope: ReaderLayerTargetScope
    target_key: str = Field(min_length=1)
    status: Literal["published"] = "published"
    schema_version: int = Field(ge=1)
    output: Any
    published_at: datetime


class ReaderSnapshotParsedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    policy_code: str = Field(min_length=1)
    parsed_state: ParsedDecisionState
    rationale_code: str | None = None


class ReaderSnapshotAskSupplement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplement_id: str = Field(min_length=1)
    owner: Literal["ask_supplement"] = "ask_supplement"
    anchor: ReaderUnitAnchor | ReaderTextRangeAnchor | None = None
    content: Any
    created_at: datetime


class ReaderSnapshotUserAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1)
    asset_type: Literal["highlight", "note"]
    owner: Literal["user"] = "user"
    reading_record_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    anchor: ReaderUnitAnchor | ReaderTextRangeAnchor
    note_text: str | None = None
    color: str | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class ReaderEnhancementProgressLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: ReaderEnhancementCapability
    layer_type: ReaderLayerType | None = None
    status: ReaderEnhancementProgressLayerStatus
    job_status: ReaderJobStatus | None = None
    job_type: str | None = Field(default=None, min_length=1)
    layer_id: str | None = Field(default=None, min_length=1)
    job_id: str | None = Field(default=None, min_length=1)
    target_type: str | None = Field(default=None, min_length=1)
    target_scope: ReaderLayerTargetScope | None = None
    target_key: str | None = Field(default=None, min_length=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    failure_code: str | None = Field(default=None, min_length=1)
    failure_message: str | None = Field(default=None, min_length=1, max_length=240)


class ReaderEnhancementProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_status: ReaderEnhancementProgressOverallStatus
    layers: list[ReaderEnhancementProgressLayer] = Field(default_factory=list)


class ReaderPlateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_kind: Literal["reader_plate_snapshot"] = "reader_plate_snapshot"
    snapshot_id: str = Field(min_length=1)
    snapshot_taken_at: datetime
    last_event_sequence: int = Field(ge=0)
    record_id: str = Field(min_length=1)
    record: ReaderSnapshotRecord
    base: ReaderSnapshotBase
    navigation: ReaderSnapshotNavigation
    anchor_segments: list[ReaderSnapshotAnchorSegment] = Field(default_factory=list)
    enhancement_layers: list[ReaderSnapshotLayer] = Field(default_factory=list)
    enhancement_progress: ReaderEnhancementProgress
    ask_supplements: list[ReaderSnapshotAskSupplement] = Field(default_factory=list)
    user_assets: list[ReaderSnapshotUserAsset] = Field(default_factory=list)
    parsed_decisions: list[ReaderSnapshotParsedDecision] = Field(default_factory=list)
    value: list[dict[str, Any]] = Field(default_factory=list)


class ReaderPlainTextSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plain_text: str = Field(min_length=1)
    title: str | None = None
    language: str | None = None
    source_metadata: dict[str, Any] | None = None
    client_record_id: str | None = Field(default=None, max_length=255)
    reading_goal: ReaderOrchestrationReadingGoal = Field(
        default=DEFAULT_READER_ORCHESTRATION_READING_GOAL
    )
    reading_variant: ReaderOrchestrationReadingVariant = Field(
        default=DEFAULT_READER_ORCHESTRATION_READING_VARIANT
    )

    @field_validator("plain_text")
    @classmethod
    def validate_plain_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("plain_text must not be blank")
        return value

    @field_validator("client_record_id")
    @classmethod
    def normalize_client_record_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("source_metadata")
    @classmethod
    def _reject_reserved_strategy_keys(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return _reject_reserved_strategy_keys_in_source_metadata(value)

    @model_validator(mode="after")
    def _validate_reader_strategy_pair(self) -> ReaderPlainTextSubmitRequest:
        _validate_reader_orchestration_strategy(
            reading_goal=self.reading_goal,
            reading_variant=self.reading_variant,
        )
        return self


class ReaderPlainTextSubmitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    article_ready_sequence: int = Field(ge=1)
    snapshot: ReaderPlateSnapshot


class ReaderStableReadyInputSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: InputAdapterSourceType
    text: str = Field(min_length=1)
    filename: str | None = None
    source_metadata: dict[str, Any] | None = None
    client_record_id: str | None = Field(default=None, max_length=255)
    language: str | None = None
    reading_goal: ReaderOrchestrationReadingGoal = Field(
        default=DEFAULT_READER_ORCHESTRATION_READING_GOAL
    )
    reading_variant: ReaderOrchestrationReadingVariant = Field(
        default=DEFAULT_READER_ORCHESTRATION_READING_VARIANT
    )

    @field_validator("text")
    @classmethod
    def validate_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @field_validator("client_record_id")
    @classmethod
    def normalize_client_record_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("source_metadata")
    @classmethod
    def _reject_reserved_strategy_keys(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return _reject_reserved_strategy_keys_in_source_metadata(value)

    @model_validator(mode="after")
    def _validate_reader_strategy_pair(self) -> ReaderStableReadyInputSubmitRequest:
        _validate_reader_orchestration_strategy(
            reading_goal=self.reading_goal,
            reading_variant=self.reading_variant,
        )
        return self


class ReaderStableReadyInputSubmitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    stable_document_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    document_version: int = Field(ge=1)
    title: str | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    block_count: int = Field(ge=1)
    article_ready_event_id: str = Field(min_length=1)
    article_ready_sequence: int = Field(ge=1)
    suitability: InputSuitabilityResult
    snapshot: ReaderPlateSnapshot


class ReaderUnifiedInputSubmitRequest(ReaderStableReadyInputSubmitRequest):
    pass


class ReaderUnifiedInputSubmitStableResponse(ReaderStableReadyInputSubmitResponse):
    outcome: Literal["stable_document_ready"]


class ReaderUnifiedInputSubmitCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["candidate_document_required"]
    reading_record_id: str = Field(min_length=1)
    candidate_document_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    status: CandidateReadingDocumentStatus
    title: str | None = None
    block_count: int = Field(ge=1)
    source_type: InputAdapterSourceType
    filename: str | None = None
    original_input_id: str = Field(min_length=1)
    suitability: InputSuitabilityResult


class ReaderUnifiedInputSubmitRejectedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["input_rejected_or_action_required"]
    suitability: InputSuitabilityResult


ReaderUnifiedInputSubmitResponse = Annotated[
    ReaderUnifiedInputSubmitStableResponse
    | ReaderUnifiedInputSubmitCandidateResponse
    | ReaderUnifiedInputSubmitRejectedResponse,
    Field(discriminator="outcome"),
]


class ReaderSourceArtifactUploadInitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_kind: SourceArtifactKind
    source_filename: str | None = None
    content_type: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reading_record_id: UUID | None = None
    original_input_id: UUID | None = None
    source_refs: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    quality: dict[str, Any] | None = None

    @field_validator("artifact_kind")
    @classmethod
    def validate_artifact_kind_for_upload_init(cls, value: SourceArtifactKind) -> SourceArtifactKind:
        if value != "original_upload":
            raise ValueError(
                "artifact_kind must be original_upload for init-upload; derived artifacts are worker-managed"
            )
        return value


class ReaderSourceArtifactUploadInitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    artifact_kind: SourceArtifactKind
    storage_provider: SourceArtifactStorageProvider
    bucket: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    status: SourceArtifactStatus
    content_type: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_filename: str = Field(min_length=1)
    upload_method: Literal[
        "oss_put_object_pending_credentials",
        "oss_put_object_presigned",
    ]
    headers: dict[str, str]
    # D6-I3Q: presigned upload URL. ``None`` when the server has no presigner
    # configured (``oss_put_object_pending_credentials``); populated when a
    # presigner returns a signed URL (``oss_put_object_presigned``).
    # The URL carries the signature in the query string and may include the
    # AccessKey id (``OSSAccessKeyId=...``) per the standard OSS presigned-URL
    # model — the id is not a secret. The AccessKey secret is never returned.
    presigned_url: str | None = None
    presigned_method: Literal["PUT"] | None = None
    presigned_expires_at: datetime | None = None


class ReaderSourceArtifactUploadCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    metadata: dict[str, Any] | None = None
    quality: dict[str, Any] | None = None


class ReaderSourceArtifactUploadCompleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    artifact_kind: SourceArtifactKind
    storage_provider: SourceArtifactStorageProvider
    bucket: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    status: SourceArtifactStatus
    content_type: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_filename: str = Field(min_length=1)
    upload_completed: Literal[True]
    idempotent_noop: bool


class ReaderSourceArtifactSubmitInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    language: str | None = None
    client_record_id: str | None = Field(default=None, max_length=255)
    source_metadata: dict[str, Any] | None = None
    reading_goal: ReaderOrchestrationReadingGoal = Field(
        default=DEFAULT_READER_ORCHESTRATION_READING_GOAL
    )
    reading_variant: ReaderOrchestrationReadingVariant = Field(
        default=DEFAULT_READER_ORCHESTRATION_READING_VARIANT
    )

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("client_record_id")
    @classmethod
    def normalize_client_record_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("source_metadata")
    @classmethod
    def _reject_reserved_strategy_keys(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return _reject_reserved_strategy_keys_in_source_metadata(value)

    @model_validator(mode="after")
    def _validate_reader_strategy_pair(self) -> ReaderSourceArtifactSubmitInputRequest:
        _validate_reader_orchestration_strategy(
            reading_goal=self.reading_goal,
            reading_variant=self.reading_variant,
        )
        return self


class ReaderSourceArtifactSubmitInputResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    original_input_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    source_type: ReaderArtifactInputSourceType
    input_type: ReaderArtifactOriginalInputType
    product_state: ReadingRecordProductState
    readiness_state: ReadingRecordReadinessState
    title: str = Field(min_length=1)
    language: str | None = None
    extraction_required: Literal[True]
    bucket: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    content_type: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_filename: str = Field(min_length=1)
    extraction_job_id: str = Field(min_length=1)
    extraction_job_status: str = Field(min_length=1)


class ReaderCandidateDocumentConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str | None = None


class ReaderCandidateDocumentConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    candidate_document_id: str = Field(min_length=1)
    stable_document_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    document_version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    block_count: int = Field(ge=1)
    candidate_confirmed: bool
    freeze_idempotent_noop: bool
    article_ready_event_id: str = Field(min_length=1)
    article_ready_sequence: int = Field(ge=1)
    snapshot: ReaderPlateSnapshot


class ReaderStableDocumentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_utf16_length: int = Field(ge=1)
    canonicalizer_version: str = Field(min_length=1)
    builder_version: str = Field(min_length=1)
    segmenter_version: str = Field(min_length=1)
    language: str | None = None
    title_snapshot: str | None = None
    navigation: dict[str, Any] = Field(default_factory=dict)


class ReaderStableDocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_document_id: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    title: str | None = None
    language: str | None = None
    source_profile: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(min_length=1)


class ReaderStableDocumentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1)
    parent_block_id: str | None = None
    order_index: int = Field(ge=0)
    block_type: str = Field(min_length=1)
    text_content: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    source_refs: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    canonical_text_start_utf16: int | None = Field(default=None, ge=0)
    canonical_text_end_utf16: int | None = Field(default=None, ge=0)
    interpretation_policy: dict[str, Any] = Field(default_factory=dict)


class ReaderStableDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    active_base_id: str = Field(min_length=1)
    base: ReaderStableDocumentBase
    stable_document: ReaderStableDocumentMetadata
    blocks: list[ReaderStableDocumentBlock] = Field(min_length=1)


class ReaderEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    reading_record_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_run_id: str | None = None
    source_job_id: str | None = None
    source_layer_id: str | None = None
    created_at: datetime


class ReaderRecordListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    title: str | None = None
    created_at: datetime
    source_type: str = Field(min_length=1)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    product_state: ReadingRecordProductState
    readiness_state: ReadingRecordReadinessState
    last_event_sequence: int = Field(ge=0)


class ReaderRecordListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReaderRecordListItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)


class ReaderEventPollResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    after_sequence: int = Field(ge=0)
    next_after_sequence: int = Field(ge=0)
    last_event_sequence: int = Field(ge=0)
    has_more: bool = False
    truncated: bool = False
    reload_required: bool = False
    reload_reason: str | None = None
    events: list[ReaderEventResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# D6-I3V Artifact Input Pipeline Status (read-only)
# ---------------------------------------------------------------------------

ReaderArtifactPipelineOutcome = Literal[
    "upload_pending",
    "upload_available_not_submitted",
    "extraction_queued",
    "extraction_running",
    "extraction_retry_later",
    "extraction_failed",
    "materialization_queued",
    "materialization_running",
    "materialization_retry_later",
    "materialization_failed",
    "stable_document_ready",
    "candidate_document_required",
    "input_rejected_or_action_required",
]

ReaderArtifactPipelineNextAction = Literal[
    "complete_upload",
    "submit_input",
    "wait_for_worker",
    "retry_later",
    "show_error",
    "open_reader",
    "confirm_candidate_document",
    "revise_input",
]


class ReaderArtifactPipelineArtifactSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    storage_provider: str = Field(min_length=1)
    bucket: str | None = None
    endpoint: str | None = None
    object_key: str = Field(min_length=1)
    content_type: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_filename: str | None = None
    reading_record_id: str | None = None
    original_input_id: str | None = None


class ReaderArtifactPipelineRecordSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    product_state: ReadingRecordProductState
    readiness_state: ReadingRecordReadinessState
    active_base_id: str | None = None
    source_type: str = Field(min_length=1)
    title: str | None = None
    language: str | None = None


class ReaderArtifactPipelineOriginalInputSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_input_id: str = Field(min_length=1)
    input_type: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    has_source_text: bool
    extraction_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReaderArtifactPipelineJobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    failure_class: str | None = None
    failure_code: str | None = None
    rationale_code: str | None = None
    available_at: datetime
    updated_at: datetime


class ReaderArtifactPipelineCandidateDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_document_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    canonical_text_preview: str


class ReaderArtifactPipelineStableDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_document_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReaderArtifactPipelineStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: ReaderArtifactPipelineArtifactSummary
    record: ReaderArtifactPipelineRecordSummary | None = None
    original_input: ReaderArtifactPipelineOriginalInputSummary | None = None
    extraction_job: ReaderArtifactPipelineJobSummary | None = None
    materialization_job: ReaderArtifactPipelineJobSummary | None = None
    candidate_document: ReaderArtifactPipelineCandidateDocument | None = None
    stable_document: ReaderArtifactPipelineStableDocument | None = None
    outcome: ReaderArtifactPipelineOutcome
    next_action: ReaderArtifactPipelineNextAction


# ---------------------------------------------------------------------------
# D6-I4T Article RAG Index Lifecycle API (read-only status + ensure trigger)
#
# The lifecycle service exposes two typed result dataclasses; these schemas
# mirror them for the HTTP boundary. ``user_id`` is intentionally NOT
# returned on either response — it is sourced only from ``AuthUserDep`` and
# exposing it would let clients depend on an internal identity field.
#
# No chunk text / embedding vector / vector payload / Plate JSON /
# Markdown syntax / DOM selection / Slate path / UI display group is ever
# present in these schemas.
# ---------------------------------------------------------------------------

ReaderArticleRagIndexLifecycleStatusValue = Literal[
    "not_ready",
    "not_indexed",
    "queued",
    "indexing",
    "indexed",
    "failed",
    "superseded_or_stale",
    "unavailable",
]

ReaderArticleRagIndexEnsureStatusValue = Literal[
    "enqueued",
    "idempotent_noop",
    "not_ready",
    "no_active_base",
    "generation_mismatch",
    "record_not_found",
    "plan_hash_mismatch",
    "bootstrap_inconsistent",
    "error",
]


class ReaderArticleRagIndexStatusResponse(BaseModel):
    """GET /reader/records/{record_id}/article-rag-index/status response.

    Mirrors ``ArticleRagIndexLifecycleStatus`` minus ``user_id``.
    """

    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    status: ReaderArticleRagIndexLifecycleStatusValue
    stable_document_id: str | None = None
    base_id: str | None = None
    record_generation: int | None = Field(default=None, ge=1)
    index_run_id: str | None = None
    index_version: str | None = Field(default=None, min_length=1)
    plan_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    chunk_count: int | None = Field(default=None, ge=0)
    reason_code: str | None = None


class ReaderArticleRagIndexEnsureRequest(BaseModel):
    """POST /reader/records/{record_id}/article-rag-index/ensure body.

    ``extra="forbid"`` so unknown fields (including ``user_id`` and
    ``chunker_version``) are rejected with 422.  ``user_id`` is sourced
    only from ``AuthUserDep``; ``chunker_version`` is intentionally not
    exposed to clients (I4S: bootstrap plan service is the authority).
    """

    model_config = ConfigDict(extra="forbid")

    expected_generation: int = Field(ge=1)
    index_version: str | None = Field(default=None, min_length=1)


class ReaderArticleRagIndexEnsureResponse(BaseModel):
    """POST /reader/records/{record_id}/article-rag-index/ensure response.

    Mirrors ``ArticleRagIndexEnsureResult`` minus ``user_id``.
    """

    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    status: ReaderArticleRagIndexEnsureStatusValue
    reason_code: str = Field(min_length=1)
    idempotent_noop: bool
    stable_document_id: str | None = None
    base_id: str | None = None
    record_generation: int | None = Field(default=None, ge=1)
    index_run_id: str | None = None
    job_id: str | None = None
    index_version: str | None = Field(default=None, min_length=1)
    chunker_version: str | None = Field(default=None, min_length=1)
