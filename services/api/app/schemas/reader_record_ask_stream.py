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
    """Safe progress signal (no raw document text / tool args)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_version: Literal["reader_record_ask_agentic_v1"] = EXECUTION_VERSION_AGENTIC_V1
    phase: str
    summary: str


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
