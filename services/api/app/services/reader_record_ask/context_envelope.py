"""Reading Record Ask — server-owned Context Envelope (contract only).

This module freezes the *server-side* context envelope for the future
independent Reading Record Ask agent.  It deliberately does **not**:

- enter the production Ask stream / LLM loop;
- call Article RAG or invent ``rag_substrate_id``;
- import any pre-cutover Ask runtime or agent modules.

Construction rule
-----------------
The factory accepts **already-verified** record / base / generation / anchor
facts.  It never re-validates against the database and never fabricates a
``visible_range`` when the client did not supply one.

Two projections
---------------
1. ``ReadingRecordAskContextEnvelope`` — server-only, may hold identity,
   generation fence material, and capability state.
2. ``ReadingRecordAskAgentContextProjection`` — safe view for the model.
   Authorization fields (``user_id``, record/base/generation, stable document,
   RAG substrate, source scope) are **absent** so the model cannot resubmit
   or override them as tool parameters.

Web search mode (G0-b5)
-----------------------
The envelope carries the user-visible ``web_search_mode`` request toggle
(``disabled`` | ``allowed``). ``allowed`` only grants turn capability; it
never forces a search and never implies provider readiness. The mode is
fingerprint-stable so retry / replay observes the same toggle. Capability
readiness (whether the resolved provider/protocol actually supports search
this turn) is **not** part of the envelope — it lives in
:class:`ResolvedWebSearchCapability` and may change across retry without
rewriting the fence identity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.reader_record_ask.web_search_contracts import WebSearchMode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENVELOPE_VERSION: Literal["reading_record_ask_context_envelope_v1"] = (
    "reading_record_ask_context_envelope_v1"
)

# Fields that are server-owned authorization / scope boundaries.
# Agent projections and model-facing tool inputs must never accept these.
SERVER_OWNED_SCOPE_FIELDS: frozenset[str] = frozenset(
    {
        "user_id",
        "tenant_id",
        "reading_record_id",
        "record_id",
        "base_id",
        "record_generation",
        "generation",
        "stable_document_id",
        "rag_substrate_id",
        "source_scope",
        "allowed_source_scope",
        "envelope_fingerprint",
    }
)


# ---------------------------------------------------------------------------
# Envelope component DTOs
# ---------------------------------------------------------------------------


class EnvelopeInitialAnchor(BaseModel):
    """Stable initial selection after server-side anchor validation.

    Offsets are unit-local UTF-16 code units, matching the editorial-asset
    / Reading Record anchor contract.  Hash is the validated ``fnv1a32-utf16``
    of ``selected_text``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    unit_id: str = Field(min_length=1)
    anchor_segment_id: str = Field(min_length=1)
    offset_unit: Literal["utf16"] = "utf16"
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    selected_text: str = Field(min_length=1)
    text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")
    hash_algorithm: Literal["fnv1a32-utf16"] = "fnv1a32-utf16"
    # Optional base-relative span from the validated segment; never fabricated.
    base_start_utf16: int | None = Field(default=None, ge=0)
    base_end_utf16: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _end_after_start(self) -> EnvelopeInitialAnchor:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        if (
            self.base_start_utf16 is not None
            and self.base_end_utf16 is not None
            and self.base_end_utf16 <= self.base_start_utf16
        ):
            raise ValueError("base_end_utf16 must be greater than base_start_utf16")
        return self


class EnvelopeVisibleRange(BaseModel):
    """Optional client viewport / visible-unit hint.

    This is an **initial context hint only**, not an authorization boundary.
    When the request omits a visible range, the envelope field must remain
    ``None`` — callers must not invent a full-document range.

    Completeness rule
    -----------------
    At least one *complete, sortable* axis is required:

    - unit-id span: both ``start_unit_id`` and ``end_unit_id``; or
    - order-index span: both ``start_unit_order_index`` and
      ``end_unit_order_index`` with ``end >= start``.

    Empty objects (``{}``), half-specified pairs, or unpaired fields are
    rejected.  Absence of a visible range is expressed by the envelope
    field being ``None``, not by an empty DTO.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    start_unit_id: str | None = Field(default=None, min_length=1)
    end_unit_id: str | None = Field(default=None, min_length=1)
    start_unit_order_index: int | None = Field(default=None, ge=0)
    end_unit_order_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_complete_sortable_span(self) -> EnvelopeVisibleRange:
        has_start_id = self.start_unit_id is not None
        has_end_id = self.end_unit_id is not None
        if has_start_id != has_end_id:
            raise ValueError(
                "visible range unit-id span requires both start_unit_id and end_unit_id"
            )
        has_start_order = self.start_unit_order_index is not None
        has_end_order = self.end_unit_order_index is not None
        if has_start_order != has_end_order:
            raise ValueError(
                "visible range order-index span requires both "
                "start_unit_order_index and end_unit_order_index"
            )
        has_id_span = has_start_id and has_end_id
        has_order_span = has_start_order and has_end_order
        if not has_id_span and not has_order_span:
            raise ValueError(
                "visible range requires at least one complete unit-id span "
                "or order-index span; omit the field (None) when absent"
            )
        if (
            has_order_span
            and self.end_unit_order_index is not None
            and self.start_unit_order_index is not None
            and self.end_unit_order_index < self.start_unit_order_index
        ):
            raise ValueError(
                "end_unit_order_index must be >= start_unit_order_index"
            )
        return self


class EnvelopeCapabilityState(BaseModel):
    """Server-side feature / capability flags for the turn.

    These flags describe what the *server* knows is available for this
    envelope.  They do not grant the model extra scope parameters.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_state: str = Field(min_length=1)
    readiness_state: str = Field(min_length=1)
    has_initial_anchor: bool
    has_visible_range: bool
    # Structural tool affordances for the future agent (schema only this round).
    can_read_range: bool = True
    can_search_current_article: bool = True
    # Article RAG substrate readiness is intentionally not decided here;
    # leave a explicit flag so later wiring does not invent substrate ids.
    article_rag_ready: bool = False
    # User-visible web search request toggle (G0-b5). ``allowed`` only
    # grants turn capability; provider/protocol readiness is resolved
    # separately by :class:`ResolvedWebSearchCapability` and is not part
    # of the envelope. The model never reads this flag directly — the
    # runtime mounts the ``search_web`` tool only when the resolved
    # capability has ``enabled_for_turn=True``.
    web_search_mode: WebSearchMode = "disabled"


# ---------------------------------------------------------------------------
# Server-only envelope
# ---------------------------------------------------------------------------


class ReadingRecordAskContextEnvelope(BaseModel):
    """Immutable server-owned Context Envelope for one Ask turn.

    May be persisted (fingerprint / snapshot) and used as a generation fence.
    Must never be accepted wholesale from the model as tool input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: Literal["reading_record_ask_context_envelope_v1"] = ENVELOPE_VERSION
    envelope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    # Authorization / identity boundaries (server-only).
    user_id: UUID
    reading_record_id: UUID
    base_id: UUID
    record_generation: int = Field(ge=1)
    # Present only when an active Stable Reading Document is already known.
    # ``None`` means "not attached / not resolved" — never a forged id.
    stable_document_id: UUID | None = None
    # Content hash of the active base when available (generation fence material).
    # Matches StableReadingBase / reader_orchestration ``content_sha256``:
    # lowercase hex SHA-256 (64 chars).
    base_content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    initial_anchor: EnvelopeInitialAnchor | None = None
    # ASK-UX-COT-COMPOSER-R3 P2 — the full canonical user-focus anchor set
    # (≤4: one auto + three pinned, gate-validated). ``initial_anchor`` is the primary selection
    # (focus_anchors[0] when the plural field is present); the remaining
    # anchors enter the model view as additional focus selections.
    # ``None`` = legacy single-anchor / no-anchor turns (fingerprint and
    # behavior identical to the pre-plural contract).
    focus_anchors: tuple[EnvelopeInitialAnchor, ...] | None = Field(
        default=None, max_length=4
    )
    # Missing client viewport stays ``None``; do not invent a range.
    visible_range: EnvelopeVisibleRange | None = None

    capabilities: EnvelopeCapabilityState

    def to_agent_projection(self) -> ReadingRecordAskAgentContextProjection:
        """Project a model-safe view that omits all server-owned scope fields."""
        selection_preview: str | None = None
        initial_selection_locator: AgentInitialSelectionLocator | None = None
        if self.initial_anchor is not None:
            text = self.initial_anchor.selected_text
            selection_preview = text if len(text) <= 240 else text[:240]
            # Restricted locator only — not an auth boundary. The executor
            # still clamps every read against the server envelope scope.
            initial_selection_locator = AgentInitialSelectionLocator(
                unit_id=self.initial_anchor.unit_id,
                anchor_segment_id=self.initial_anchor.anchor_segment_id,
                offset_unit=self.initial_anchor.offset_unit,
                start_offset=self.initial_anchor.start_offset,
                end_offset=self.initial_anchor.end_offset,
            )
        return ReadingRecordAskAgentContextProjection(
            envelope_version=self.envelope_version,
            has_initial_selection=self.initial_anchor is not None,
            selection_preview=selection_preview,
            initial_selection_locator=initial_selection_locator,
            has_visible_range=self.visible_range is not None,
            can_read_range=self.capabilities.can_read_range,
            can_search_current_article=self.capabilities.can_search_current_article,
            article_rag_ready=self.capabilities.article_rag_ready,
            readiness_state=self.capabilities.readiness_state,
            # G0-b5: surface only the request toggle, never provider/protocol.
            # The runtime still gates tool mounting on the resolved
            # capability's ``enabled_for_turn``; this flag tells the model
            # the user *allowed* web search this turn (never forces a call).
            web_search_allowed=self.capabilities.web_search_mode == "allowed",
        )


# ---------------------------------------------------------------------------
# Agent / tool projection (model-visible, no auth fields)
# ---------------------------------------------------------------------------


class AgentInitialSelectionLocator(BaseModel):
    """Model-visible locator for the turn's initial selection.

    Contains only unit/segment/offset business fields so the model can
    call ``read_range`` against the selection without receiving
    record/base/generation or other authorization fields.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    unit_id: str = Field(min_length=1)
    anchor_segment_id: str = Field(min_length=1)
    offset_unit: Literal["utf16"] = "utf16"
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)


class ReadingRecordAskAgentContextProjection(BaseModel):
    """Safe context projection for the model / agent system prompt.

    Intentionally excludes ``user_id``, record/base/generation, stable
    document, RAG substrate, source scope, and the envelope fingerprint.
    Tools must read those boundaries from server deps, not from this DTO.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: Literal["reading_record_ask_context_envelope_v1"]
    has_initial_selection: bool
    selection_preview: str | None = None
    # Limited unit/segment locator for the initial selection (not auth).
    initial_selection_locator: AgentInitialSelectionLocator | None = None
    has_visible_range: bool
    can_read_range: bool
    can_search_current_article: bool
    article_rag_ready: bool
    readiness_state: str
    # G0-b5: surfacing only the user-visible request toggle (never the
    # provider/protocol readiness state). The runtime still gates tool
    # mounting on the resolved capability's ``enabled_for_turn``; this
    # flag tells the model the user *allowed* web search this turn
    # (never forces a call).
    web_search_allowed: bool = False


# ---------------------------------------------------------------------------
# Factory input (already verified)
# ---------------------------------------------------------------------------


class VerifiedEnvelopeInput(BaseModel):
    """Already-verified facts used to construct an envelope.

    Callers must complete record / base / generation / anchor validation
    *before* building this input.  The factory does not hit the database.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    reading_record_id: UUID
    base_id: UUID
    record_generation: int = Field(ge=1)
    stable_document_id: UUID | None = None
    base_content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    product_state: str = Field(min_length=1)
    readiness_state: str = Field(min_length=1)
    initial_anchor: EnvelopeInitialAnchor | None = None
    # R3 P2 — full canonical focus anchor set (≤4); see the envelope field.
    focus_anchors: tuple[EnvelopeInitialAnchor, ...] | None = Field(
        default=None, max_length=4
    )
    # Pass through only when the client supplied a *validated* range.
    # Omit or set ``None`` when absent — never invent a full-document range.
    visible_range: EnvelopeVisibleRange | None = None
    # Optional overrides for future feature flags; defaults are conservative.
    can_read_range: bool = True
    can_search_current_article: bool = True
    article_rag_ready: bool = False
    # G0-b5: user-visible web search request toggle. ``disabled`` is the
    # safe default — capability is not granted unless the request
    # explicitly sets ``allowed``. This is the request-mode only; provider
    # / protocol readiness is resolved separately by
    # :class:`ResolvedWebSearchCapability` and is NOT part of the
    # envelope. The mode enters the fingerprint so retry / replay
    # observes the same toggle.
    web_search_mode: WebSearchMode = "disabled"


# ---------------------------------------------------------------------------
# Fingerprint + factory
# ---------------------------------------------------------------------------


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def compute_envelope_fingerprint(
    *,
    envelope_version: str,
    user_id: UUID,
    reading_record_id: UUID,
    base_id: UUID,
    record_generation: int,
    stable_document_id: UUID | None,
    base_content_sha256: str | None,
    initial_anchor: EnvelopeInitialAnchor | None,
    visible_range: EnvelopeVisibleRange | None,
    web_search_mode: WebSearchMode = "disabled",
    focus_anchors: tuple[EnvelopeInitialAnchor, ...] | None = None,
) -> str:
    """Deterministic SHA-256 hex fingerprint for persistence / generation fence.

    Capability product states are intentionally excluded so a readiness
    transition alone does not rewrite the fence identity of a turn that
    already started under a fixed document/base/generation/anchor.

    ``web_search_mode`` is the user-visible *request* toggle (not a
    capability readiness state) and IS part of the fence identity so
    retry / replay observes the same toggle. Provider / protocol
    readiness (:class:`ResolvedWebSearchCapability`) is excluded — it
    may change across retry without rewriting the fence identity.

    R3 P2: the canonical focus anchor set (when non-empty) is part of the
    fence identity — a different user focus selection is a different turn
    context. Empty/absent focus keeps the payload byte-identical to the
    pre-plural contract, so legacy fingerprints are stable.
    """
    payload: dict[str, Any] = {
        "envelope_version": envelope_version,
        "user_id": str(user_id),
        "reading_record_id": str(reading_record_id),
        "base_id": str(base_id),
        "record_generation": record_generation,
        "stable_document_id": (
            str(stable_document_id) if stable_document_id is not None else None
        ),
        "base_content_sha256": base_content_sha256,
        "initial_anchor": (
            initial_anchor.model_dump(mode="json") if initial_anchor is not None else None
        ),
        "visible_range": (
            visible_range.model_dump(mode="json") if visible_range is not None else None
        ),
        "web_search_mode": web_search_mode,
    }
    # R3 P2 — include the focus set only when non-empty so legacy
    # (no focus) fingerprints stay byte-identical.
    if focus_anchors:
        payload["focus_anchors"] = [
            anchor.model_dump(mode="json") for anchor in focus_anchors
        ]
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def build_context_envelope(verified: VerifiedEnvelopeInput) -> ReadingRecordAskContextEnvelope:
    """Build a server-owned envelope from already-verified inputs.

    ``visible_range`` is copied as-is: missing stays ``None``.
    """
    fingerprint = compute_envelope_fingerprint(
        envelope_version=ENVELOPE_VERSION,
        user_id=verified.user_id,
        reading_record_id=verified.reading_record_id,
        base_id=verified.base_id,
        record_generation=verified.record_generation,
        stable_document_id=verified.stable_document_id,
        base_content_sha256=verified.base_content_sha256,
        initial_anchor=verified.initial_anchor,
        visible_range=verified.visible_range,
        web_search_mode=verified.web_search_mode,
        focus_anchors=verified.focus_anchors,
    )
    capabilities = EnvelopeCapabilityState(
        product_state=verified.product_state,
        readiness_state=verified.readiness_state,
        has_initial_anchor=verified.initial_anchor is not None,
        has_visible_range=verified.visible_range is not None,
        can_read_range=verified.can_read_range,
        can_search_current_article=verified.can_search_current_article,
        article_rag_ready=verified.article_rag_ready,
        web_search_mode=verified.web_search_mode,
    )
    return ReadingRecordAskContextEnvelope(
        envelope_version=ENVELOPE_VERSION,
        envelope_fingerprint=fingerprint,
        user_id=verified.user_id,
        reading_record_id=verified.reading_record_id,
        base_id=verified.base_id,
        record_generation=verified.record_generation,
        stable_document_id=verified.stable_document_id,
        base_content_sha256=verified.base_content_sha256,
        initial_anchor=verified.initial_anchor,
        focus_anchors=verified.focus_anchors,
        visible_range=verified.visible_range,
        capabilities=capabilities,
    )
