"""SSE / persistence DTO for the agentic Reading Record Ask path.

``ReaderRecordAskCompletedDTO`` is the single truth object: the same
serialization is written to turn-run persistence and emitted on
``message.completed``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EXECUTION_VERSION_AGENTIC_V1 = "reader_record_ask_agentic_v1"

FinalStatus = Literal[
    "ok",
    "context_stale",
    "invalid_citations",
    "failed",
    "cancelled",
]

EvidenceKindPublic = Literal[
    "initial_anchor",
    "read_range",
    "search_hit",
    "observation",
]

ProgressPhase = Literal[
    "agent_running",
    "reading_context",
    "searching_article",
    "composing_answer",
    "validating_evidence",
]

ProgressActivity = Literal[
    "started",
    "completed",
    "unavailable",
    "failed",
]

ProgressToolName = Literal[
    "read_range",
    "search_current_article",
]

ProgressStatus = Literal[
    "running",
    "ok",
    "unavailable",
    "failed",
]


class ReaderRecordAskRagCitationPublic(BaseModel):
    """Public RAG citation fields safe for SSE / thread reload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rag_substrate_id: str
    index_run_id: str
    index_version: str
    plan_content_sha256: str
    source_scope: Literal["main_reading_text", "heading"]
    block_type: str
    chunk_id: str
    content_sha256: str
    canonical_text_start_utf16: int
    canonical_text_end_utf16: int
    snippet: str
    score: float | None = None
    # Identity for Web generation fence / stable navigation (next round).
    stable_document_id: str
    base_id: str
    record_generation: int = Field(ge=1)
    block_ids: list[str] = Field(default_factory=list)
    unit_ids: list[str] = Field(default_factory=list)
    anchor_segment_ids: list[str] = Field(default_factory=list)


class ReaderRecordAskEvidenceItem(BaseModel):
    """One resolved evidence item for completed DTO / persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle_id: str
    kind: EvidenceKindPublic
    source_tool: str
    snippet: str | None = None
    unit_id: str | None = None
    anchor_segment_id: str | None = None
    rag_citation: ReaderRecordAskRagCitationPublic | None = None


class ReaderRecordAskCompletedDTO(BaseModel):
    """Canonical completed payload for SSE and DB.

    Only finalizer ``ok`` paths produce this as a successful completed
    event. Stale/invalid paths use :class:`ReaderRecordAskTerminalDTO`
    instead and must not put a displayable answer on completed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_version: Literal["reader_record_ask_agentic_v1"] = EXECUTION_VERSION_AGENTIC_V1
    final_status: Literal["ok"] = "ok"
    answer_text: str
    message_id: str
    thread_id: str
    turn_run_id: str
    envelope_fingerprint: str
    evidence: list[ReaderRecordAskEvidenceItem] = Field(default_factory=list)


class ReaderRecordAskTerminalDTO(BaseModel):
    """Typed non-ok terminal (stale / invalid citations / cancelled / failed).

    Emitted as ``message.interrupted`` or ``error`` depending on status.
    Never carries a displayable answer for stale/invalid.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_version: Literal["reader_record_ask_agentic_v1"] = EXECUTION_VERSION_AGENTIC_V1
    final_status: FinalStatus
    message_id: str | None = None
    thread_id: str | None = None
    turn_run_id: str | None = None
    envelope_fingerprint: str | None = None
    terminal_reason: str | None = None
    rejected_handles: list[str] = Field(default_factory=list)


class ReaderRecordAskRunStartedDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_version: Literal["reader_record_ask_agentic_v1"] = EXECUTION_VERSION_AGENTIC_V1
    message_id: str
    thread_id: str
    turn_run_id: str
    envelope_fingerprint: str
    has_initial_selection: bool


class ReaderRecordAskProgressDTO(BaseModel):
    """Safe live activity signal for ``agentic.progress``.

    Privacy-safe projection only: no tool args, document text, evidence
    handles, fingerprints, provider payloads, or chain-of-thought.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_version: Literal["reader_record_ask_agentic_v1"] = EXECUTION_VERSION_AGENTIC_V1
    sequence: int = Field(ge=1)
    phase: ProgressPhase
    activity: ProgressActivity
    summary: str = Field(min_length=1, max_length=120)
    elapsed_ms: int = Field(ge=0)
    tool_name: ProgressToolName | None = None
    status: ProgressStatus | None = None
    duration_ms: int | None = Field(default=None, ge=0)


def evidence_item_from_observation(obs: Any) -> ReaderRecordAskEvidenceItem:
    """Project a server observation to the public evidence item."""
    handle = obs.handle
    rag_public = None
    rag = getattr(obs, "rag_citation", None)
    if rag is not None:
        rag_public = ReaderRecordAskRagCitationPublic(
            rag_substrate_id=rag.rag_substrate_id,
            index_run_id=rag.index_run_id,
            index_version=rag.index_version,
            plan_content_sha256=rag.plan_content_sha256,
            source_scope=rag.source_scope,
            block_type=rag.block_type,
            chunk_id=rag.chunk_id,
            content_sha256=rag.content_sha256,
            canonical_text_start_utf16=rag.canonical_text_start_utf16,
            canonical_text_end_utf16=rag.canonical_text_end_utf16,
            snippet=rag.snippet,
            score=rag.score,
            stable_document_id=rag.stable_document_id,
            base_id=rag.base_id,
            record_generation=rag.record_generation,
            block_ids=list(rag.block_ids),
            unit_ids=list(rag.unit_ids),
            anchor_segment_ids=list(rag.anchor_segment_ids),
        )
    return ReaderRecordAskEvidenceItem(
        handle_id=handle.handle_id,
        kind=handle.kind,
        source_tool=handle.source_tool,
        snippet=obs.snippet,
        unit_id=obs.unit_id,
        anchor_segment_id=obs.anchor_segment_id,
        rag_citation=rag_public,
    )


# ---------------------------------------------------------------------------
# History / thread-detail DTOs (cold-load only)
#
# Distinct from Analysis Ask ReaderAskMessage so that wire contract stays
# isolated. Reuses the same strict field types as ReaderAskMessage and only
# adds agentic history fields with a strict evidence schema.
# ---------------------------------------------------------------------------

from app.schemas.reader_ask import (  # noqa: E402
    ReaderAskActionProposal,
    ReaderAskAnchorRef,
    ReaderAskArticleRagCitation,
    ReaderAskArticleRagSidecar,
    ReaderAskAssetDisambiguation,
    ReaderAskCitation,
    ReaderAskContextPlan,
    ReaderAskDisambiguation,
    ReaderAskEvidenceItem,
    ReaderAskFollowUpSuggestion,
    ReaderAskMessageRole,
    ReaderAskMessageStatus,
    ReaderAskPersistedSupplement,
    ReaderAskResolvedContextInput,
    ReaderAskResolvedContextSummary,
    ReaderAskResolvedIntent,
    ReaderAskResponseCard,
    ReaderAskRunInfo,
    ReaderAskSelectedModel,
    ReaderAskSubmissionMode,
    ReaderAskSupplementCandidate,
    ReaderAskToolTraceEntry,
    ReaderAskTraceSummary,
)


class ReaderRecordAskHistoryMessage(BaseModel):
    """Reading Record Ask thread-detail message (legacy + agentic)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    thread_id: str
    role: ReaderAskMessageRole
    status: ReaderAskMessageStatus
    content_md: str
    submission_mode: ReaderAskSubmissionMode = "chat"
    resolved_intent: ReaderAskResolvedIntent | None = None
    context_anchors: list[ReaderAskAnchorRef] = Field(default_factory=list)
    citations: list[ReaderAskCitation] = Field(default_factory=list)
    action_proposals: list[ReaderAskActionProposal] = Field(default_factory=list)
    tool_trace: list[ReaderAskToolTraceEntry] = Field(default_factory=list)
    # Legacy evidence only. Agentic / quarantined rows always emit [].
    evidence: list[ReaderAskEvidenceItem] = Field(default_factory=list)
    trace_summary: ReaderAskTraceSummary | None = None
    disambiguation: ReaderAskDisambiguation | None = None
    external_asset_disambiguation: ReaderAskAssetDisambiguation | None = None
    response_cards: list[ReaderAskResponseCard] = Field(default_factory=list)
    resolved_context: ReaderAskResolvedContextSummary | None = None
    context_plan: ReaderAskContextPlan | None = None
    resolved_context_input: ReaderAskResolvedContextInput | None = None
    run_info: ReaderAskRunInfo | None = None
    supplement_candidates: list[ReaderAskSupplementCandidate] = Field(default_factory=list)
    persisted_supplements: list[ReaderAskPersistedSupplement] = Field(default_factory=list)
    reasoning_md: str | None = None
    reasoning_status: Literal["idle", "streaming", "completed"] | None = None
    usage_event_id: str | None = None
    follow_up_suggestions: list[ReaderAskFollowUpSuggestion] | None = None
    article_rag: ReaderAskArticleRagSidecar | None = None
    article_rag_citations: list[ReaderAskArticleRagCitation] = Field(default_factory=list)
    # Agentic-only history fields. Omitted for legacy RR messages via
    # response_model_exclude_none on the RR thread-detail route.
    execution_version: Literal["reader_record_ask_agentic_v1"] | None = None
    final_status: FinalStatus | None = None
    agentic_evidence: list[ReaderRecordAskEvidenceItem] | None = None
    created_at: str
    updated_at: str


class ReaderRecordAskThreadDetail(BaseModel):
    """Reading Record Ask thread detail (history reload)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    record_id: str
    title: str | None = None
    is_default: bool
    selected_model: ReaderAskSelectedModel | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str
    last_message_at: str | None = None
    messages: list[ReaderRecordAskHistoryMessage] = Field(default_factory=list)
