"""Reading Record Ask — server observation / evidence handle contracts.

Purpose
-------
Reserve a typed handle contract for the future evidence finalizer:

- Handles are **minted only by the tool executor** (or other server-side
  observation registry).
- The model may only **reference** handles that were previously returned.
- This slice does **not** decide the authoritative source of
  ``rag_substrate_id`` and does not modify the legacy article_rag sidecar DTO.

No tool executor or finalizer is implemented here.
"""

from __future__ import annotations

import re
import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EVIDENCE_HANDLE_PREFIX = "evh_"
_HANDLE_ID_PATTERN = re.compile(r"^evh_[0-9a-f]{32}$")
_ENVELOPE_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")

EvidenceKind = Literal[
    "initial_anchor",
    "read_range",
    "search_hit",
    "observation",
    "article_seed",
]

# Agent-callable tool names that may produce evidence. Retained as a
# three-value Literal distinct from :data:`EvidenceOrigin` so the agent
# tool interface stays narrow (``baseline_context`` is a server-only origin
# and must never appear as an agent-callable tool name).
EvidenceSourceTool = Literal[
    "initial_anchor",
    "read_range",
    "search_current_article",
]

# Server-side evidence provenance. Strict superset of
# :data:`EvidenceSourceTool` adding ``baseline_context`` (the full-article
# baseline seed origin produced by the baseline context assembler, never an
# agent-callable tool), ``selection_expand`` (the host-owned opaque
# selection expansion seam, ) and ``map_expand`` (the host-owned
# article-map cursor expansion seam, — map entries themselves are
# never evidence; only text produced by expanding a map cursor is). None
# of the expansion origins is an agent-callable tool name; the model only
# supplies opaque pointers. Server evidence handles / minting / legal-map
# use this strict six-value type.
EvidenceOrigin = Literal[
    "initial_anchor",
    "read_range",
    "search_current_article",
    "baseline_context",
    "selection_expand",
    "map_expand",
]

# Legal (kind, source) pairs.  Rejects inconsistent combinations such as
# kind=initial_anchor + source=baseline_context.
LEGAL_EVIDENCE_KIND_SOURCE: dict[EvidenceKind, frozenset[EvidenceOrigin]] = {
    "initial_anchor": frozenset({"initial_anchor"}),
    "read_range": frozenset({"read_range"}),
    "search_hit": frozenset({"search_current_article"}),
    # Generic observation may be produced by any first-wave source or by
    # the host-owned expansion seams (selection, map).
    "observation": frozenset(
        {
            "initial_anchor",
            "read_range",
            "search_current_article",
            "selection_expand",
            "map_expand",
        }
    ),
    # article_seed is exclusively produced by the baseline context assembler.
    # It must NOT carry initial_anchor / read_range / search_current_article
    # because the full-article baseline is not a user selection, a tool-driven
    # read range, or a RAG search hit.
    "article_seed": frozenset({"baseline_context"}),
}


def assert_legal_evidence_kind_source(
    kind: EvidenceKind,
    source_tool: EvidenceOrigin,
) -> None:
    """Raise ``ValueError`` when kind/source are inconsistent."""
    allowed = LEGAL_EVIDENCE_KIND_SOURCE.get(kind)
    if allowed is None or source_tool not in allowed:
        raise ValueError(
            f"illegal evidence kind/source combination: kind={kind!r}, "
            f"source_tool={source_tool!r}; allowed sources for this kind: "
            f"{sorted(allowed) if allowed else []}"
        )


# ---------------------------------------------------------------------------
# Model-facing citation ref (opaque handle only)
# ---------------------------------------------------------------------------


class EvidenceHandleRef(BaseModel):
    """What the model is allowed to cite in a final answer.

    Only the opaque ``handle_id`` is accepted.  Server binding fields
    (envelope fingerprint, locator payloads, snippets) live in the
    server registry and must not be reconstructed by the model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    handle_id: str = Field(min_length=1, max_length=64)

    @field_validator("handle_id")
    @classmethod
    def _validate_handle_shape(cls, value: str) -> str:
        if not _HANDLE_ID_PATTERN.match(value):
            raise ValueError(
                "handle_id must be a server-minted token matching "
                f"{EVIDENCE_HANDLE_PREFIX}<32 hex chars>"
            )
        return value


# ---------------------------------------------------------------------------
# Server-owned handle + observation (executor registry material)
# ---------------------------------------------------------------------------


class ServerEvidenceHandle(BaseModel):
    """Server-generated evidence handle identity.

    Must be created via :func:`mint_server_evidence_handle` (or an
    equivalent executor helper).  Do not accept full handle objects
    from the model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle_id: str = Field(min_length=1, max_length=64)
    kind: EvidenceKind
    # Bind the handle to the turn envelope so finalizer can fence generation.
    envelope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_tool: EvidenceOrigin

    @field_validator("handle_id")
    @classmethod
    def _validate_handle_shape(cls, value: str) -> str:
        if not _HANDLE_ID_PATTERN.match(value):
            raise ValueError(
                "handle_id must be a server-minted token matching "
                f"{EVIDENCE_HANDLE_PREFIX}<32 hex chars>"
            )
        return value

    @model_validator(mode="after")
    def _validate_kind_source_pair(self) -> ServerEvidenceHandle:
        assert_legal_evidence_kind_source(self.kind, self.source_tool)
        return self


class ArticleRagCitationEvidence(BaseModel):
    """Server-owned Article RAG citation fields (not free-form dict).

    ``rag_substrate_id`` is the immutable ``reader_article_rag_index_runs.id``
    string.  Never invent this value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rag_substrate_id: str = Field(min_length=1)
    index_run_id: str = Field(min_length=1)
    plan_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_scope: Literal["main_reading_text", "heading"]
    block_type: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_text_start_utf16: int = Field(ge=0)
    canonical_text_end_utf16: int = Field(gt=0)
    snippet: str = Field(min_length=1, max_length=2000)
    score: float | None = None
    reading_record_id: str = Field(min_length=1)
    stable_document_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    block_ids: tuple[str, ...] = ()
    unit_ids: tuple[str, ...] = ()
    anchor_segment_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _range_and_substrate(self) -> ArticleRagCitationEvidence:
        if self.canonical_text_end_utf16 <= self.canonical_text_start_utf16:
            raise ValueError("canonical_text_end_utf16 must be > start")
        if self.rag_substrate_id != self.index_run_id:
            raise ValueError(
                "rag_substrate_id must equal index_run_id (index-run identity)"
            )
        return self


class ServerEvidenceObservation(BaseModel):
    """Full server-side observation registered by a tool executor.

    Finalizers resolve model-cited :class:`EvidenceHandleRef` values
    against a registry of these observations.  Unknown, unused, or
    generation-mismatched handles must be rejected at finalize time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle: ServerEvidenceHandle
    # Optional human-readable snippet / locator summary for citation UX.
    snippet: str | None = Field(default=None, max_length=2000)
    locator_summary: dict[str, Any] | None = None
    # Optional unit / segment pointers (record-local; not cross-record).
    unit_id: str | None = Field(default=None, min_length=1)
    anchor_segment_id: str | None = Field(default=None, min_length=1)
    # Typed RAG citation — never stuff rag_substrate_id into free dicts.
    rag_citation: ArticleRagCitationEvidence | None = None

    @model_validator(mode="after")
    def _reject_substrate_authority_fields(
        self,
    ) -> ServerEvidenceObservation:
        if self.locator_summary is not None:
            forbidden = {
                "rag_substrate_id",
                "user_id",
                "tenant_id",
                "index_run_id",
            }
            offenders = forbidden.intersection(self.locator_summary.keys())
            if offenders:
                raise ValueError(
                    "locator_summary must not carry authority fields: "
                    + ", ".join(sorted(offenders))
                )
        if self.handle.kind == "search_hit" and self.rag_citation is None:
            raise ValueError("search_hit observations require rag_citation")
        if self.rag_citation is not None and self.handle.kind != "search_hit":
            raise ValueError("rag_citation is only valid for search_hit kind")
        return self


# ---------------------------------------------------------------------------
# Minting helpers (server-side only)
# ---------------------------------------------------------------------------


def mint_evidence_handle_id() -> str:
    """Generate a new opaque evidence handle id.

    Only tool executors / observation registries should call this.
    """
    return f"{EVIDENCE_HANDLE_PREFIX}{secrets.token_hex(16)}"


def mint_server_evidence_handle(
    *,
    kind: EvidenceKind,
    envelope_fingerprint: str,
    source_tool: EvidenceOrigin,
    handle_id: str | None = None,
) -> ServerEvidenceHandle:
    """Mint a server-owned evidence handle bound to an envelope fingerprint.

    ``handle_id`` may be supplied only for deterministic tests; production
    executors should leave it ``None`` so a fresh opaque token is generated.
    """
    if not _ENVELOPE_FINGERPRINT_PATTERN.match(envelope_fingerprint):
        raise ValueError(
            "envelope_fingerprint must be a 64-char lowercase hex SHA-256 digest"
        )
    assert_legal_evidence_kind_source(kind, source_tool)
    return ServerEvidenceHandle(
        handle_id=handle_id or mint_evidence_handle_id(),
        kind=kind,
        envelope_fingerprint=envelope_fingerprint,
        source_tool=source_tool,
    )


def build_server_evidence_observation(
    *,
    kind: EvidenceKind,
    envelope_fingerprint: str,
    source_tool: EvidenceOrigin,
    snippet: str | None = None,
    locator_summary: dict[str, Any] | None = None,
    unit_id: str | None = None,
    anchor_segment_id: str | None = None,
    handle_id: str | None = None,
    rag_citation: ArticleRagCitationEvidence | None = None,
) -> ServerEvidenceObservation:
    """Convenience constructor for a full server observation entry."""
    handle = mint_server_evidence_handle(
        kind=kind,
        envelope_fingerprint=envelope_fingerprint,
        source_tool=source_tool,
        handle_id=handle_id,
    )
    return ServerEvidenceObservation(
        handle=handle,
        snippet=snippet,
        locator_summary=locator_summary,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        rag_citation=rag_citation,
    )


def is_valid_evidence_handle_id(handle_id: str) -> bool:
    """Return True when ``handle_id`` matches the server mint shape."""
    return bool(_HANDLE_ID_PATTERN.match(handle_id))


def parse_evidence_handle_ref(handle_id: str) -> EvidenceHandleRef:
    """Parse a model-cited handle ref; raises on illegal shape."""
    return EvidenceHandleRef(handle_id=handle_id)
