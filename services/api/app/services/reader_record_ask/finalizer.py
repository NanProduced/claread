"""Evidence finalizer for Reading Record Ask.

Consumes host-validated answer blocks, re-checks the generation fence,
resolves opaque handles against the turn registry, and produces an
**internal** final result. Public DTOs are projected later by the stream
layer; this module never writes SSE or DB rows.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
from app.services.reader_record_ask.web_evidence_registry import WebEvidenceRegistry
from app.services.reader_record_ask.web_search_contracts import (
    WEB_DESCRIPTION_MAX_LEN,
    WEB_TITLE_MAX_LEN,
    WEB_URL_MAX_LEN,
    PublicWebSearchSummary,
    WebEvidence,
    WebSearchOutcome,
    canonicalize_url,
    normalize_provider_published_at,
)

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
    """Message-local public citation (no internal handles or locator blobs).

    Web citations carry ``url`` / ``title`` / ``description`` instead of
    a snippet; article citations carry an optional ``snippet``. The
    discriminator is ``source_kind``. Web fields are optional so article
    citations do not need to populate them, but web citations must
    populate ``url`` and ``title`` (architecture brief §5).

    ASK-WEB-G1-R3: this is the single canonical public citation contract.
    The previous ``PublicWebCitation`` class in ``web_search_contracts``
    has been removed — it duplicated a subset of this contract with
    identical field validation. Web citations must carry a canonical URL
    (validated at the contract layer via :func:`canonicalize_url`) and a
    non-empty title. Title fallback to ``display_domain`` is applied by
    the production finalizer before constructing the citation — the
    contract itself does not derive fallbacks.

    Public JSON never carries: ``evh_`` handles, ``handle_id``, envelope
    or source fingerprint, ``provider_result_ref``, ``query`` / ``rank``
    / ``score`` / raw payload.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str = Field(min_length=1, max_length=32)
    source_kind: Literal["article", "web"]
    snippet: str | None = None
    # Web-specific fields (G0-b3). Required when ``source_kind="web"``;
    # ignored for article citations. v1 exposes only url / title /
    # description — no provider, query, rank, score, or internal handle.
    url: str | None = Field(default=None, max_length=WEB_URL_MAX_LEN)
    title: str | None = Field(default=None, max_length=WEB_TITLE_MAX_LEN)
    description: str | None = Field(
        default=None, max_length=WEB_DESCRIPTION_MAX_LEN
    )
    # Publication and retrieval are deliberately distinct. ``published_at``
    # is present only when a provider supplied a strict ISO calendar date;
    # ``retrieved_at`` is host-recorded and must be labeled "retrieved" by UI.
    # Raw provider ``page_age`` is never a public field.
    published_at: str | None = Field(default=None, max_length=10)
    retrieved_at: str | None = Field(default=None, max_length=64)

    @field_validator("url")
    @classmethod
    def _validate_canonical_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        canonical = canonicalize_url(value)
        if canonical != value:
            raise ValueError(
                "url must already be in canonical form; route provider URLs "
                "through canonicalize_url()"
            )
        return value

    @field_validator("published_at", mode="before")
    @classmethod
    def _validate_published_at(cls, value: object) -> str | None:
        return normalize_provider_published_at(value)

    @model_validator(mode="after")
    def _validate_web_citation_fields(self) -> PublicCitation:
        if self.source_kind == "web":
            if self.url is None:
                raise ValueError("web citation requires url")
            if self.title is None or not self.title.strip():
                raise ValueError("web citation requires a non-empty title")
            if self.snippet is not None:
                raise ValueError("web citation must not carry an article snippet")
        else:
            if self.url is not None:
                raise ValueError("article citation must not carry url")
            if self.title is not None:
                raise ValueError("article citation must not carry title")
            if self.description is not None:
                raise ValueError(
                    "article citation must not carry description"
                )
            if self.published_at is not None:
                raise ValueError("article citation must not carry published_at")
            if self.retrieved_at is not None:
                raise ValueError("article citation must not carry retrieved_at")
        return self


class InternalCitationBinding(BaseModel):
    """Restricted server-only citation → evidence binding for navigation/audit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str
    handle_id: str
    source_kind: Literal["article", "web"]
    snippet: str | None = None
    # Web-specific binding material (internal-only; never on public DTO).
    canonical_url: str | None = None
    web_title: str | None = None
    web_description: str | None = None
    published_at: str | None = None
    retrieved_at: str | None = None
    source_fingerprint: str | None = None
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
    # Turn-level web search outcome summary (G0-b3). ``None`` means
    # search was not invoked this turn. Set on every grounded_answer
    # finalize path; ``None`` on clarification / source_unavailable /
    # unavailable terminals.
    web_search_summary: PublicWebSearchSummary | None = None


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


def _binding_from_web_evidence(
    *,
    citation_id: str,
    web_evidence: WebEvidence,
) -> InternalCitationBinding:
    """Build an internal-only citation binding from one web evidence entry.

    Web bindings carry ``canonical_url`` / ``web_title`` /
    ``web_description`` / ``retrieved_at`` / ``source_fingerprint`` so
    server-side audit / navigation can re-verify identity without
    re-trusting provider text. These fields never appear on the public
    citation DTO.
    """
    return InternalCitationBinding(
        citation_id=citation_id,
        handle_id=web_evidence.internal_handle_id,
        source_kind="web",
        snippet=None,
        canonical_url=web_evidence.canonical_url,
        web_title=web_evidence.title,
        web_description=web_evidence.description,
        published_at=web_evidence.published_at,
        retrieved_at=web_evidence.retrieved_at,
        source_fingerprint=web_evidence.source_fingerprint,
        unit_id=None,
        anchor_segment_id=None,
        kind="web",
        source_tool="search_web",
        rag_citation=None,
    )


async def finalize_agent_answer(
    *,
    envelope: ReadingRecordAskContextEnvelope,
    registry: EvidenceRegistry,
    fence: FenceFn,
    response_kind: ResponseKind,
    validated_answer_blocks: ValidatedAnswerBlocks | None = None,
    clarification_text: str | None = None,
    web_evidence_registry: WebEvidenceRegistry | None = None,
    web_search_outcome: WebSearchOutcome | None = None,
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
    - Web blocks (G0-b3): when ``web_evidence_registry`` is provided,
      web-block handles are resolved against it and emitted as
      ``source_kind="web"`` :class:`PublicCitation` entries with
      url/title/description from :class:`WebEvidence`. When it is
      ``None``, any web block fails closed as ``invalid_citations``.
    - ``web_search_summary`` is set on the grounded_answer path only when
      ``web_search_outcome`` is non-``None`` (search was invoked). It
      counts only message-local web citations actually attached to the
      answer — not the raw provider result count.
    """
    fp = envelope.envelope_fingerprint

    if registry.envelope_fingerprint != fp:
        return FinalizedAskResult(
            status="invalid_citations",
            reason="evidence registry is not bound to this envelope",
            envelope_fingerprint=fp,
            response_kind=response_kind,
        )

    # Defense-in-depth: web registry must be bound to the same envelope.
    if (
        web_evidence_registry is not None
        and web_evidence_registry.envelope_fingerprint != fp
    ):
        return FinalizedAskResult(
            status="invalid_citations",
            reason="web evidence registry is not bound to this envelope",
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

    # Defense-in-depth: web blocks require a web evidence registry.
    has_web_blocks = any(block.basis == "web" for block in validated_answer_blocks.blocks)
    if has_web_blocks and web_evidence_registry is None:
        return FinalizedAskResult(
            status="invalid_citations",
            reason="web answer blocks require a web evidence registry",
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
    resolved_web_evidence: list[WebEvidence] = []
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

        # Article registry first (the common path).
        observation = registry.get(raw_id)
        if observation is not None:
            if observation.handle.envelope_fingerprint != fp:
                rejected.append(raw_id)
                continue
            resolved_observations.append(observation)
            continue

        # Web registry fallback (G0-b3).
        if web_evidence_registry is not None:
            try:
                web_ev = web_evidence_registry.get(raw_id)
            except ValueError:
                # source_fingerprint mismatch — fail closed.
                rejected.append(raw_id)
                continue
            if web_ev is not None:
                resolved_web_evidence.append(web_ev)
                continue

        rejected.append(raw_id)

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
    web_by_handle = {
        ev.internal_handle_id: ev for ev in resolved_web_evidence
    }
    public_citations: list[PublicCitation] = []
    bindings: list[InternalCitationBinding] = []
    for handle_id in ordered_handles:
        citation_id = handle_to_citation[handle_id]
        if handle_id in obs_by_handle:
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
        else:
            web_ev = web_by_handle[handle_id]
            # ASK-WEB-G1-R2: provider may not supply a title. The single
            # canonical production fallback is the WebEvidence's
            # ``display_domain`` (already extracted from the canonical URL
            # at registration time). Tests must NOT manually patch the
            # title to satisfy the contract — the finalizer is the only
            # place where this fallback is applied.
            web_title = web_ev.title or web_ev.display_domain
            public_citations.append(
                PublicCitation(
                    citation_id=citation_id,
                    source_kind="web",
                    snippet=None,
                    url=web_ev.canonical_url,
                    title=web_title,
                    description=web_ev.description,
                    published_at=web_ev.published_at,
                    retrieved_at=web_ev.retrieved_at,
                )
            )
            bindings.append(
                _binding_from_web_evidence(
                    citation_id=citation_id,
                    web_evidence=web_ev,
                )
            )

    public_blocks: list[PublicAnswerBlock] = []
    for block in validated_answer_blocks.blocks:
        if block.basis == "general":
            public_blocks.append(
                PublicAnswerBlock(text=block.text, citation_ids=[])
            )
            continue
        # article + web share the same citation-id mapping flow.
        citation_ids = [
            handle_to_citation[h]
            for h in block.evidence_handles
            if h in handle_to_citation
        ]
        if len(citation_ids) != len(block.evidence_handles):
            return FinalizedAskResult(
                status="invalid_citations",
                reason="answer block evidence handles failed public citation mapping",
                envelope_fingerprint=fp,
                response_kind=response_kind,
            )
        public_blocks.append(
            PublicAnswerBlock(text=block.text, citation_ids=citation_ids)
        )

    answer_text = "\n\n".join(block.text for block in public_blocks)

    # G0-b3: build the turn-level web search summary. ``None`` when the
    # search was not invoked this turn (no outcome supplied). The cited
    # count is the number of message-local public web citations actually
    # attached to the answer — not the raw provider result count.
    web_search_summary: PublicWebSearchSummary | None = None
    if web_search_outcome is not None:
        cited_web_count = sum(
            1 for c in public_citations if c.source_kind == "web"
        )
        web_search_summary = PublicWebSearchSummary(
            outcome=web_search_outcome,
            cited_source_count=cited_web_count,
        )

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
        web_search_summary=web_search_summary,
    )
