"""Evidence finalizer for Reading Record Ask.

Consumes host-validated answer blocks, re-checks the generation fence,
resolves opaque handles against the turn registry, and produces an
**internal** final result. Public DTOs are projected later by the stream
layer; this module never writes SSE or DB rows.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.reader_record_ask.answer_block_provenance import (
    KnowledgeMode,
    ValidatedAnswerBlocks,
)
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)
from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    ServerEvidenceObservation,
    is_valid_evidence_handle_id,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import FenceFn, run_fence

# Internal-only finalize status. Wire FinalStatus lives in
# reader_record_ask_stream.py. ``unavailable`` is internal and is mapped by
# production_stream to wire ``final_status="failed"`` + typed terminal_reason
# except for the dedicated source_unavailable ok-completed path.
FinalizeStatus = Literal[
    "ok",
    "context_stale",
    "invalid_citations",
    "unavailable",
]

# Internal-only response discriminator. Never enters public SSE / completed /
# history DTO fields as a free-form model string beyond host projection rules.
ResponseKind = Literal[
    "grounded_answer",
    "clarification",
    "source_unavailable",
    "unavailable",
]

SourceStatus = Literal["article_source_unavailable"]

SOURCE_UNAVAILABLE_ANSWER_TEXT = "无法在当前文章中可靠定位原文。"


class PublicAnswerBlock(BaseModel):
    """Message-local public answer block (no internal handles)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=8_000)
    citation_ids: list[str] = Field(default_factory=list)


class PublicCitation(BaseModel):
    """Message-local public citation (no internal handles or locator blobs)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str = Field(min_length=1, max_length=32)
    source_kind: Literal["article", "web"]
    snippet: str | None = None


class InternalCitationBinding(BaseModel):
    """Restricted server-only citation → evidence binding for navigation/audit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str
    handle_id: str
    source_kind: Literal["article", "web"]
    snippet: str | None = None
    unit_id: str | None = None
    anchor_segment_id: str | None = None
    kind: str
    source_tool: str
    rag_citation: dict | None = None


class FinalizedAskResult(BaseModel):
    """Internal final result after handle resolution + fence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: FinalizeStatus
    answer_text: str | None = None
    answer_blocks: tuple[PublicAnswerBlock, ...] = ()
    public_citations: tuple[PublicCitation, ...] = ()
    knowledge_mode: KnowledgeMode | None = None
    source_status: SourceStatus | None = None
    # Internal-only: ordered observations for restricted persistence.
    resolved_evidence: tuple[ServerEvidenceObservation, ...] = ()
    # Internal-only: citation_id → evidence binding (same order as public_citations).
    citation_bindings: tuple[InternalCitationBinding, ...] = ()
    rejected_handles: tuple[str, ...] = ()
    reason: str | None = None
    envelope_fingerprint: str
    response_kind: ResponseKind | None = None


def _binding_from_observation(
    *,
    citation_id: str,
    observation: ServerEvidenceObservation,
) -> InternalCitationBinding:
    rag = observation.rag_citation
    return InternalCitationBinding(
        citation_id=citation_id,
        handle_id=observation.handle.handle_id,
        source_kind="article",
        snippet=observation.snippet,
        unit_id=observation.unit_id,
        anchor_segment_id=observation.anchor_segment_id,
        kind=str(observation.handle.kind),
        source_tool=str(observation.handle.source_tool),
        rag_citation=rag.model_dump(mode="json") if rag is not None else None,
    )


async def finalize_agent_answer(
    *,
    envelope: ReadingRecordAskContextEnvelope,
    registry: EvidenceRegistry,
    fence: FenceFn,
    response_kind: ResponseKind,
    validated_answer_blocks: ValidatedAnswerBlocks | None = None,
    clarification_text: str | None = None,
) -> FinalizedAskResult:
    """Finalize validated blocks into a fence-checked, handle-resolved result.

    Responsibility scope:
    - Registry must be bound to this envelope fingerprint.
    - Final generation fence.
    - Handle resolution + envelope match (defense-in-depth). Provenance
      semantics (basis, scope, handle existence for model retries) live in
      the output validator; this path fails closed without silent repair.
    - ``source_unavailable`` → ok completed projection with host-owned copy.
    - ``unavailable`` → internal terminal mapped by production_stream.
    """
    fp = envelope.envelope_fingerprint

    if registry.envelope_fingerprint != fp:
        return FinalizedAskResult(
            status="invalid_citations",
            reason="evidence registry is not bound to this envelope",
            envelope_fingerprint=fp,
            response_kind=response_kind,
        )

    if response_kind == "unavailable":
        return FinalizedAskResult(
            status="unavailable",
            answer_text=None,
            resolved_evidence=(),
            rejected_handles=(),
            reason=None,
            envelope_fingerprint=fp,
            response_kind=response_kind,
        )

    if response_kind == "source_unavailable":
        fence_result = await run_fence(fence, envelope)
        if not fence_result.ok:
            return FinalizedAskResult(
                status="context_stale",
                reason=fence_result.reason or "generation mismatch at finalize",
                envelope_fingerprint=fp,
                response_kind=response_kind,
            )
        return FinalizedAskResult(
            status="ok",
            answer_text=SOURCE_UNAVAILABLE_ANSWER_TEXT,
            answer_blocks=(),
            public_citations=(),
            knowledge_mode=None,
            source_status="article_source_unavailable",
            resolved_evidence=(),
            citation_bindings=(),
            rejected_handles=(),
            envelope_fingerprint=fp,
            response_kind=response_kind,
        )

    if response_kind == "clarification":
        fence_result = await run_fence(fence, envelope)
        if not fence_result.ok:
            return FinalizedAskResult(
                status="context_stale",
                reason=fence_result.reason or "generation mismatch at finalize",
                envelope_fingerprint=fp,
                response_kind=response_kind,
            )
        text = (clarification_text or "").strip()
        if not text:
            return FinalizedAskResult(
                status="invalid_citations",
                reason="clarification requires non-empty clarification_text",
                envelope_fingerprint=fp,
                response_kind=response_kind,
            )
        return FinalizedAskResult(
            status="ok",
            answer_text=text,
            answer_blocks=(PublicAnswerBlock(text=text, citation_ids=[]),),
            public_citations=(),
            knowledge_mode=None,
            source_status=None,
            resolved_evidence=(),
            citation_bindings=(),
            rejected_handles=(),
            envelope_fingerprint=fp,
            response_kind=response_kind,
        )

    # grounded_answer
    if validated_answer_blocks is None:
        return FinalizedAskResult(
            status="invalid_citations",
            reason="grounded answer requires validated answer blocks",
            envelope_fingerprint=fp,
            response_kind=response_kind,
        )

    fence_result = await run_fence(fence, envelope)
    if not fence_result.ok:
        return FinalizedAskResult(
            status="context_stale",
            reason=fence_result.reason or "generation mismatch at finalize",
            envelope_fingerprint=fp,
            response_kind=response_kind,
        )

    # First-seen handle order across blocks → stable c1, c2, …
    handle_to_citation: dict[str, str] = {}
    ordered_handles: list[str] = []
    for block in validated_answer_blocks.blocks:
        for handle_id in block.evidence_handles:
            if handle_id in handle_to_citation:
                continue
            citation_id = f"c{len(ordered_handles) + 1}"
            handle_to_citation[handle_id] = citation_id
            ordered_handles.append(handle_id)

    resolved_observations: list[ServerEvidenceObservation] = []
    rejected: list[str] = []
    for raw_id in ordered_handles:
        if not is_valid_evidence_handle_id(raw_id):
            rejected.append(raw_id)
            continue
        try:
            EvidenceHandleRef(handle_id=raw_id)
        except Exception:  # noqa: BLE001
            rejected.append(raw_id)
            continue

        observation = registry.get(raw_id)
        if observation is None:
            rejected.append(raw_id)
            continue
        if observation.handle.envelope_fingerprint != fp:
            rejected.append(raw_id)
            continue
        resolved_observations.append(observation)

    if rejected:
        return FinalizedAskResult(
            status="invalid_citations",
            reason=(
                "one or more cited evidence handles are unknown, foreign, "
                "or malformed"
            ),
            rejected_handles=tuple(rejected),
            envelope_fingerprint=fp,
            response_kind=response_kind,
        )

    obs_by_handle = {
        obs.handle.handle_id: obs for obs in resolved_observations
    }
    public_citations: list[PublicCitation] = []
    bindings: list[InternalCitationBinding] = []
    for handle_id in ordered_handles:
        citation_id = handle_to_citation[handle_id]
        observation = obs_by_handle[handle_id]
        public_citations.append(
            PublicCitation(
                citation_id=citation_id,
                source_kind="article",
                snippet=observation.snippet,
            )
        )
        bindings.append(
            _binding_from_observation(
                citation_id=citation_id,
                observation=observation,
            )
        )

    public_blocks: list[PublicAnswerBlock] = []
    for block in validated_answer_blocks.blocks:
        if block.basis == "general":
            public_blocks.append(
                PublicAnswerBlock(text=block.text, citation_ids=[])
            )
            continue
        if block.basis == "web":
            # Defense-in-depth: provenance validator rejects web in v1.
            return FinalizedAskResult(
                status="invalid_citations",
                reason="web answer blocks are not supported in v1",
                envelope_fingerprint=fp,
                response_kind=response_kind,
            )
        # article
        citation_ids = [
            handle_to_citation[h] for h in block.evidence_handles if h in handle_to_citation
        ]
        if len(citation_ids) != len(block.evidence_handles):
            return FinalizedAskResult(
                status="invalid_citations",
                reason="article block evidence handles failed public citation mapping",
                envelope_fingerprint=fp,
                response_kind=response_kind,
            )
        public_blocks.append(
            PublicAnswerBlock(text=block.text, citation_ids=citation_ids)
        )

    answer_text = "\n\n".join(block.text for block in public_blocks)

    return FinalizedAskResult(
        status="ok",
        answer_text=answer_text,
        answer_blocks=tuple(public_blocks),
        public_citations=tuple(public_citations),
        knowledge_mode=validated_answer_blocks.knowledge_mode,
        source_status=None,
        resolved_evidence=tuple(resolved_observations),
        citation_bindings=tuple(bindings),
        rejected_handles=(),
        envelope_fingerprint=fp,
        response_kind=response_kind,
    )
