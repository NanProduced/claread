from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.annotation import (
    TEXT_RANGE_HASH_ALGORITHM,
    TEXT_RANGE_OFFSET_UNIT,
    utf16_code_unit_length,
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
VocabularyItemType = Literal["vocab_highlight", "phrase_gloss", "context_gloss"]
ParsedDecisionState = Literal["not_started", "partial", "parsed", "skipped", "failed"]
AnchorSegmentType = Literal["sentence", "clause", "fallback_window"]
ReaderBoundaryQuality = Literal["normal", "low"]
ReaderUnitType = Literal["body", "heading", "list", "quote", "unknown", "fallback"]
ReaderLayerTargetScope = Literal["unit", "anchor_segment", "unit_range", "record"]


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
        return self


class TranslationLayerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    target_language: str = Field(min_length=1)
    translated_text: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)
    confidence: Literal["low", "normal", "high"] = "normal"


class ReaderSnapshotBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonicalizer_version: str = Field(min_length=1)
    builder_version: str = Field(min_length=1)
    segmenter_version: str = Field(min_length=1)
    text_length_utf16: int = Field(ge=1)
    hash_algorithm: Literal["fnv1a32-utf16"] = TEXT_RANGE_HASH_ALGORITHM


class ReaderSnapshotNavigationUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    order_index: int = Field(ge=1)
    unit_type: ReaderUnitType
    boundary_quality: ReaderBoundaryQuality = "normal"
    label: str | None = None
    base_start_utf16: int = Field(ge=0)
    base_end_utf16: int = Field(gt=0)


class ReaderSnapshotNavigation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[ReaderSnapshotNavigationUnit] = Field(default_factory=list)


class ReaderSnapshotLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1)
    layer_type: str = Field(min_length=1)
    layer_subtype: str | None = None
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
    asset_type: str = Field(min_length=1)
    owner: Literal["user"] = "user"
    anchor: ReaderUnitAnchor | ReaderTextRangeAnchor
    deleted_at: datetime | None = None
    updated_at: datetime


class ReaderPlateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_kind: Literal["reader_plate_snapshot"] = "reader_plate_snapshot"
    snapshot_id: str = Field(min_length=1)
    snapshot_taken_at: datetime
    last_event_sequence: int = Field(ge=0)
    record_id: str = Field(min_length=1)
    base: ReaderSnapshotBase
    navigation: ReaderSnapshotNavigation
    enhancement_layers: list[ReaderSnapshotLayer] = Field(default_factory=list)
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


class ReaderPlainTextSubmitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    article_ready_sequence: int = Field(ge=1)
    snapshot: ReaderPlateSnapshot


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
