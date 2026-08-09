from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.schemas.reader_orchestration import GrammarBundleOutput

CaptureState = Literal["started", "captured", "ambiguous"]
UsageDeliveryState = Literal[
    "not_ready",
    "pending",
    "reconciled",
    "dead_letter",
]


class PayloadContractError(ValueError):
    """A versioned receipt or usage draft failed its strict contract."""


class JournalConflictError(RuntimeError):
    """A stable execution identity was reused with different facts."""


class CaptureEnvelopeConflictError(JournalConflictError):
    """A second capture disagreed with the durable receipt."""


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    invocation_key: str
    reader_job_id: UUID | None
    reader_run_id: UUID | None
    attempt_ordinal: int
    execution_slot: int


@dataclass(frozen=True, slots=True)
class BeginDisposition:
    journal_id: UUID
    invocation_key: str
    capture_state: CaptureState
    provider_call_allowed: bool


class GrammarBatchUnitResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    output: GrammarBundleOutput


class GrammarBatchResumePayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outputs: list[GrammarBatchUnitResultV1]
    diagnostics: dict[str, JsonValue] | None = None


class DisplayTitleResumePayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_zh: str = Field(min_length=1, max_length=32)


class SemanticOutlineCandidateResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ref: str = Field(min_length=1)
    parent_candidate_ref: str | None = None
    depth: int = Field(ge=1)
    title: str = Field(min_length=1)
    start_unit_id: str = Field(min_length=1)
    end_unit_id: str = Field(min_length=1)
    start_anchor_segment_id: str | None = None
    end_anchor_segment_id: str | None = None


class SemanticOutlineResumePayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[SemanticOutlineCandidateResultV1]
    worker_failure: bool
    model: str | None = None


class UsageEventDraftV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage_scope: Literal[
        "user_billed",
        "system_internal",
        "anonymous_trial",
        "eval_debug",
    ]
    capability_code: str = Field(min_length=1)
    billing_mode: Literal[
        "user_points",
        "internal_only",
        "trial",
        "no_charge",
    ]
    status: str = Field(min_length=1)
    user_id: UUID | None = None
    reading_record_id: UUID | None = None
    reader_run_id: UUID | None = None
    reader_job_id: UUID | None = None
    enhancement_layer_id: UUID | None = None
    daily_reader_article_id: str | None = None
    client_platform: str | None = None
    request_id: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    schema_version: str | None = None
    prompt_version: str | None = None
    model_route: str = Field(min_length=1)
    model_profile_id: str | None = None
    model_profile: str | None = None
    model_provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    planner_kind: str | None = None
    policy_version: str | None = None
    cache_hit: bool | None = None
    cache_status: str | None = None
    cache_class: str | None = None
    usage_data: dict[str, JsonValue] | None = None
    cached_input_tokens: int | None = None
    cache_miss_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    token_budget_before: int | None = None
    token_budget_after: int | None = None
    latency_ms: int | None = None
    billed_points: int | None = None
    billing_policy_version: str | None = None
    operation_fingerprint: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata_json: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreparedCaptureEnvelope:
    invocation_kind: str
    resume_payload_kind: str
    resume_payload_schema_version: int
    usage_event_draft_schema_version: int
    normalized_payload: dict[str, Any]
    usage_event_draft: dict[str, Any]
    capture_envelope_sha256: str
    resume_payload_bytes: int
    usage_event_draft_bytes: int


@dataclass(frozen=True, slots=True)
class CapturedReceipt:
    journal_id: UUID
    identity: ExecutionIdentity
    invocation_kind: str
    resume_payload_kind: str
    resume_payload_schema_version: int
    usage_event_draft_schema_version: int
    normalized_payload: dict[str, Any]
    usage_event_draft: dict[str, Any]
    capture_envelope_sha256: str
    captured_at: datetime
    usage_delivery_state: UsageDeliveryState
    ai_usage_event_id: UUID | None
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class MaterializationSummary:
    scanned: int
    reconciled: int
    dead_lettered: int


@dataclass(frozen=True, slots=True)
class RecoveryDisposition:
    kind: Literal["none", "ambiguous", "captured_resume"]
    receipts: tuple[CapturedReceipt, ...] = ()
