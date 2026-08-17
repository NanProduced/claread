"""Reading Record Ask — schema-first contracts for first-wave read tools.

Tools covered (schema only; **no executors** in this slice):

- ``read_range``
- ``search_current_article``
- ``search_web`` (G1-b3 — provider-neutral web search)

Model-facing tool inputs must only carry business parameters (query,
limited locator).  Authorization fields (``user_id``, record/base/
generation, stable document, RAG substrate, source scope) are rejected
via ``extra="forbid"`` and must be taken from the server envelope/deps.

Tool outputs use a closed typed status set plus the unified shape:

    status / summary / next_actions / payloads / evidence_handles

``evidence_handles`` carries only :class:`EvidenceHandleRef` values
(server-mint shape).  Arbitrary strings are rejected.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.reader_record_ask.context_envelope import SERVER_OWNED_SCOPE_FIELDS
from app.services.reader_record_ask.evidence import EvidenceHandleRef
from app.services.reader_record_ask.web_search_contracts import (
    WEB_MAX_RESULTS_PER_CALL,
    WEB_QUERY_MAX_LEN,
)

# ---------------------------------------------------------------------------
# Tool names
# ---------------------------------------------------------------------------

TOOL_READ_RANGE: Literal["read_range"] = "read_range"
TOOL_SEARCH_CURRENT_ARTICLE: Literal["search_current_article"] = (
    "search_current_article"
)
TOOL_EXPAND_EVIDENCE: Literal["expand_evidence"] = "expand_evidence"
# G1-b3: provider-neutral web search tool (host-owned function tool).
# Mounted only when :attr:`ResolvedWebSearchCapability.enabled_for_turn`
# is True (the runtime decides; the model never reads capability state).
TOOL_SEARCH_WEB: Literal["search_web"] = "search_web"

# Production agent tools: expand_evidence + search_current_article.
# ``read_range`` remains as a legacy contract name for offline schemas only.
# G1-b4: ``search_web`` is conditionally registered when the resolved
# web search capability has ``enabled_for_turn=True``.
ReaderRecordAskReadToolName = Literal["read_range", "search_current_article"]
ReaderRecordAskProductionToolName = Literal[
    "expand_evidence", "search_current_article", "search_web"
]

# Offsets on read locators are always unit-/segment-local UTF-16 code units.
READ_RANGE_OFFSET_UNIT: Literal["utf16"] = "utf16"

ReadRangeLocatorMode = Literal[
    "whole_unit",
    "whole_segment",
    "unit_order_span",
    "unit_utf16_range",
    "segment_utf16_range",
]


# ---------------------------------------------------------------------------
# Closed status set
# ---------------------------------------------------------------------------

# Distinguishes success, empty, not-ready / not-indexed / indexing,
# budget, stale context, invalid locator, and generic failure.
# Callers must not collapse these into a bare string exception.
ReaderRecordAskToolStatus = Literal[
    "ok",
    "empty",
    "not_ready",
    "not_indexed",
    "indexing",
    "unavailable",
    "budget_exhausted",
    "invalid_locator",
    "context_stale",
    "error",
]


class ToolBudgetExhaustedView(BaseModel):
    """Bounded control-channel result when a tool content account is full.

    This view carries no evidence, source text, cursor, provider payload, or
    retry instruction. It lets the agent finish from evidence already visible
    instead of converting ordinary content pressure into a terminal failure.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["budget_exhausted"] = "budget_exhausted"
    summary: str = Field(
        default=(
            "Tool content limit reached. Continue with evidence already available."
        ),
        min_length=1,
        max_length=400,
    )


# ---------------------------------------------------------------------------
# Limited locators (business parameters only)
# ---------------------------------------------------------------------------


class ReadRangeLocator(BaseModel):
    """Locator for ``read_range`` inside the *current* envelope scope.

    Offset unit is frozen as UTF-16 code units (unit- or segment-local).
    A bare ``start_offset`` without ``end_offset`` is **illegal** — the
    executor must not guess "to unit end" or "to segment end".

    Exactly one of the following modes is allowed:

    ================  =====================================================
    Mode              Required fields
    ================  =====================================================
    whole_unit        ``unit_id`` only
    whole_segment     ``anchor_segment_id`` (+ optional ``unit_id``)
    unit_order_span   both ``start_unit_order_index`` and
                      ``end_unit_order_index`` (inclusive, end >= start)
    unit_utf16_range  ``unit_id`` + both ``start_offset`` and ``end_offset``
    segment_utf16_range
                      ``anchor_segment_id`` + both offsets
                      (+ optional ``unit_id``)
    ================  =====================================================

    Mixing order-index span with unit/segment ids, half-specified offsets,
    or half-specified order indices is rejected.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    offset_unit: Literal["utf16"] = READ_RANGE_OFFSET_UNIT
    unit_id: str | None = Field(default=None, min_length=1)
    anchor_segment_id: str | None = Field(default=None, min_length=1)
    start_unit_order_index: int | None = Field(default=None, ge=0)
    end_unit_order_index: int | None = Field(default=None, ge=0)
    # Unit-/segment-local UTF-16 offsets; both required when either is set.
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_legal_mode(self) -> ReadRangeLocator:
        has_unit = self.unit_id is not None
        has_segment = self.anchor_segment_id is not None
        has_start_order = self.start_unit_order_index is not None
        has_end_order = self.end_unit_order_index is not None
        has_start_off = self.start_offset is not None
        has_end_off = self.end_offset is not None

        if has_start_order != has_end_order:
            raise ValueError(
                "unit order span requires both start_unit_order_index and "
                "end_unit_order_index"
            )
        if has_start_off != has_end_off:
            raise ValueError(
                "utf16 range requires both start_offset and end_offset; "
                "a single offset is illegal (executor must not infer unit/segment end)"
            )
        if (
            has_start_order
            and has_end_order
            and self.end_unit_order_index is not None
            and self.start_unit_order_index is not None
            and self.end_unit_order_index < self.start_unit_order_index
        ):
            raise ValueError(
                "end_unit_order_index must be >= start_unit_order_index"
            )
        if (
            has_start_off
            and has_end_off
            and self.end_offset is not None
            and self.start_offset is not None
            and self.end_offset <= self.start_offset
        ):
            raise ValueError("end_offset must be greater than start_offset")

        has_order_span = has_start_order and has_end_order
        has_offsets = has_start_off and has_end_off
        has_identity = has_unit or has_segment

        if has_order_span and (has_identity or has_offsets):
            raise ValueError(
                "unit_order_span cannot be combined with unit_id, "
                "anchor_segment_id, or utf16 offsets"
            )
        if has_order_span:
            return self

        if has_offsets:
            if not has_identity:
                raise ValueError(
                    "utf16 range requires unit_id and/or anchor_segment_id"
                )
            return self

        # Whole target (no offsets, no order span).
        if has_unit or has_segment:
            return self

        raise ValueError(
            "read_range locator requires one of: whole_unit (unit_id), "
            "whole_segment (anchor_segment_id), unit_order_span "
            "(both order indices), unit_utf16_range, or segment_utf16_range"
        )

    def resolve_mode(self) -> ReadRangeLocatorMode:
        """Return the frozen legal mode for executor dispatch."""
        has_order = (
            self.start_unit_order_index is not None
            and self.end_unit_order_index is not None
        )
        if has_order:
            return "unit_order_span"
        has_offsets = self.start_offset is not None and self.end_offset is not None
        if has_offsets:
            if self.anchor_segment_id is not None:
                return "segment_utf16_range"
            return "unit_utf16_range"
        if self.anchor_segment_id is not None:
            return "whole_segment"
        return "whole_unit"


class ReadRangeToolInput(BaseModel):
    """Model-facing input for ``read_range``."""

    model_config = ConfigDict(extra="forbid")

    locator: ReadRangeLocator
    # Soft hint for executor truncation; not an auth boundary.
    max_chars: int | None = Field(default=None, ge=1, le=50_000)


class SearchCurrentArticleToolInput(BaseModel):
    """Model-facing input for ``search_current_article``.

    Scope is always the current envelope's record/base/document.
    The model supplies only the query (and optional result limit).

    ``limit`` mirrors the host truth exactly: 1..10 (the TurnCoordinator
    clamps to this range and defaults None to 5) — the model contract
    must never advertise values the host silently reduces.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    limit: int | None = Field(default=None, ge=1, le=10)


# ---------------------------------------------------------------------------
# Unified tool result
# ---------------------------------------------------------------------------


class ReaderRecordAskToolResult(BaseModel):
    """Unified observation shape returned by read tools.

    Executors (not defined in this slice) must return this structure
    instead of bare strings or unclassified exceptions.

    ``evidence_handles`` only accepts server-mint :class:`EvidenceHandleRef`
    values (or dicts / mint-shaped strings that validate as such).
    """

    model_config = ConfigDict(extra="forbid")

    status: ReaderRecordAskToolStatus
    summary: str = Field(min_length=1)
    next_actions: list[str] = Field(default_factory=list)
    # Tool-specific body: snippets, coverage, remaining budget, etc.
    payloads: dict[str, Any] | list[Any] | None = None
    # Opaque evidence handles minted by the tool executor (server-only mint).
    evidence_handles: list[EvidenceHandleRef] = Field(default_factory=list)

    @field_validator("next_actions", mode="before")
    @classmethod
    def _clean_next_actions(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("next_actions must be a list of strings")
        return [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]

    @field_validator("evidence_handles", mode="before")
    @classmethod
    def _coerce_evidence_handles(cls, value: object) -> list[object]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("evidence_handles must be a list")
        coerced: list[object] = []
        for item in value:
            if isinstance(item, str):
                # Bare strings must still match mint shape via EvidenceHandleRef.
                coerced.append({"handle_id": item})
            else:
                coerced.append(item)
        return coerced


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def assert_no_server_owned_fields(payload: dict[str, Any]) -> None:
    """Raise ``ValueError`` if a raw dict carries server-owned scope keys.

    Useful for defensive checks at tool-registration boundaries before
    Pydantic validation.
    """
    offenders = sorted(SERVER_OWNED_SCOPE_FIELDS.intersection(payload.keys()))
    if offenders:
        raise ValueError(
            "tool input must not include server-owned scope fields: "
            + ", ".join(offenders)
        )


# ---------------------------------------------------------------------------
# Expand-evidence schemas (isolated — NOT wired to any runtime)
# ---------------------------------------------------------------------------

EXPANSION_CURSOR_PREFIX: str = "cur_"
_EXPANSION_CURSOR_ID_PATTERN = re.compile(r"^cur_[0-9a-f]{32}$")
_HANDLE_ID_SHAPE_PATTERN = re.compile(r"^evh_[0-9a-f]{32}$")


def is_expansion_cursor_shape(token: str) -> bool:
    """Return True when ``token`` matches the server-minted cursor shape."""
    return isinstance(token, str) and bool(_EXPANSION_CURSOR_ID_PATTERN.match(token))


def is_expand_pointer_shape(token: str) -> bool:
    """Return True when ``token`` is a legal opaque expand pointer.

    Legal shapes: an ``evh_<32 hex>`` evidence handle (initial selection
    pointer) or a ``cur_<32 hex>`` server-minted continuation cursor.
    """
    return isinstance(token, str) and bool(
        _HANDLE_ID_SHAPE_PATTERN.match(token)
        or _EXPANSION_CURSOR_ID_PATTERN.match(token)
    )


ExpandEvidenceStatus = Literal["ok", "invalid_cursor", "stale_evidence"]


class ExpandEvidenceToolView(BaseModel):
    """Narrow model-visible tool-view for opaque selection expansion.

    Deliberately **not** the legacy ``ReaderRecordAskToolResult`` shape:
    ``payloads`` (free-form body stuffing) is absent by design. The logical
    article text may appear exactly once, inside ``article_text_block``
    (a renderer-minted ``<untrusted_article_text role="expand">`` block;
    XML escaping preserved).

    Field set is closed (``extra="forbid"``). No binding sidecar may exist
    here: no turn_id, envelope_fingerprint, record_generation, base_id,
    reading_record_id, scope_kind, offsets, locators, hashes, scores,
    chunk ids, or user/record/base UUIDs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ExpandEvidenceStatus
    summary: str = Field(min_length=1, max_length=400)
    next_actions: tuple[str, ...] = ()
    # Opaque server-minted evidence handle (citeable) — success only.
    evidence_handle: EvidenceHandleRef | None = None
    # Opaque server-minted continuation cursor — success with remainder only.
    next_cursor: str | None = Field(
        default=None, pattern=r"^cur_[0-9a-f]{32}$"
    )
    # Renderer-minted untrusted XML block — success only; single text copy.
    article_text_block: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_status_field_coupling(self) -> ExpandEvidenceToolView:
        if self.status == "ok":
            if self.evidence_handle is None:
                raise ValueError("ok expand tool-view requires evidence_handle")
            if self.article_text_block is None:
                raise ValueError(
                    "ok expand tool-view requires article_text_block"
                )
        else:
            # Safe error views: no handle, no cursor, no text, no actions.
            if self.evidence_handle is not None:
                raise ValueError(
                    "error expand tool-view must not carry evidence_handle"
                )
            if self.next_cursor is not None:
                raise ValueError(
                    "error expand tool-view must not carry next_cursor"
                )
            if self.article_text_block is not None:
                raise ValueError(
                    "error expand tool-view must not carry article_text_block"
                )
            if self.next_actions:
                raise ValueError(
                    "error expand tool-view must not carry next_actions"
                )
        return self


# Model-argument bound enforced by the normalization seam. Over-bound /
# non-string pointers are mapped to "" so they always reach the session's
# safe state machine (metered invalid_cursor) instead of raising.
EXPAND_POINTER_MAX_LEN: int = 64


def normalize_expand_pointer(raw_arguments: Mapping[str, Any] | str) -> str:
    """Model-argument normalization seam (offline; no runtime wiring).

    Extracts **only** the opaque pointer from raw model arguments and
    routes it to :meth:`EvidenceExpansionSession.expand`:

    - every other key (``turn_id``, ``record_generation``, ``base_id``,
      ``reading_record_id``, ``envelope_fingerprint``, …) is discarded by
      omission — model-supplied identity can never reach the server-owned
      binding;
    - **no shape rejection**: shape / unknown / consumed judgement is the
      session's job (safe metered ``invalid_cursor`` / ``stale_evidence``);
    - bounded: non-string or over-length pointers map to ``""``, which the
      session answers with a metered ``invalid_cursor`` — never raises a
      ValidationError, never model-retry.
    """
    if isinstance(raw_arguments, str):
        pointer = raw_arguments
    elif isinstance(raw_arguments, Mapping):
        value = raw_arguments.get("pointer", "")
        pointer = value if isinstance(value, str) else ""
    else:
        pointer = ""
    if len(pointer) > EXPAND_POINTER_MAX_LEN:
        return ""
    return pointer


class ExpandEvidenceToolInput(BaseModel):
    """Model-facing input for the future ``expand_evidence`` tool.

    Minimal model-visible surface: exactly one opaque ``pointer`` and
    nothing else.

    The schema itself routes **every** raw argument shape through
    :func:`normalize_expand_pointer` (``mode="before"`` validator), so
    ``model_validate`` never raises a ValidationError on missing / empty
    / non-string / over-length pointers, nor on mappings carrying
    ``turn_id`` / ``record_generation`` / ``base_id`` /
    ``reading_record_id`` / ``envelope_fingerprint`` extras — identity
    keys are discarded by the seam and normalization failure yields
    ``""``. The normalized pointer then reaches
    :meth:`EvidenceExpansionSession.expand`, where malformed / unknown /
    consumed resolve to metered ``invalid_cursor`` and a known binding
    mismatch to metered ``stale_evidence``. This is the single
    model-argument path into the core: no second route around
    normalization, no model-retry control flow, and model-supplied
    identity never influences the server-owned binding.
    """

    model_config = ConfigDict(extra="ignore")

    pointer: str

    @model_validator(mode="before")
    @classmethod
    def _route_through_normalization(cls, values: Any) -> dict[str, str]:
        return {"pointer": normalize_expand_pointer(values)}


# ---------------------------------------------------------------------------
# RAG search tool-view schema (isolated — NOT wired to any runtime)
# ---------------------------------------------------------------------------

RagSearchStatus = Literal[
    "ok",
    "empty",
    "not_ready",
    "not_indexed",
    "indexing",
    "unavailable",
]


class RagSearchToolView(BaseModel):
    """Narrow model-visible tool-view for ``search_current_article``.

    Deliberately **not** the legacy ``ReaderRecordAskToolResult`` shape:
    no free-form ``payloads``. Article text appears only inside
    ``article_text_blocks`` (renderer-minted
    ``<untrusted_article_text role="rag">`` blocks, XML escaping
    preserved; one block per cited hit). score / chunk_id / hashes /
    UUIDs / substrate / provenance / raw locators are sidecar-only and
    can never be expressed in this schema.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: RagSearchStatus
    summary: str = Field(min_length=1, max_length=400)
    next_actions: tuple[str, ...] = ()
    # Opaque server-minted evidence handles (citeable) — ok only.
    evidence_handles: tuple[EvidenceHandleRef, ...] = ()
    # Renderer-minted untrusted XML blocks — ok only; one per handle.
    article_text_blocks: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_status_field_coupling(self) -> RagSearchToolView:
        if self.status == "ok":
            if not self.evidence_handles:
                raise ValueError("ok rag tool-view requires evidence_handles")
            if not self.article_text_blocks:
                raise ValueError(
                    "ok rag tool-view requires article_text_blocks"
                )
            if len(self.evidence_handles) != len(self.article_text_blocks):
                raise ValueError(
                    "rag tool-view handles and blocks must align 1:1"
                )
        else:
            # Fail-soft safe views: no handles, no blocks, no actions.
            if self.evidence_handles:
                raise ValueError(
                    "non-ok rag tool-view must not carry evidence_handles"
                )
            if self.article_text_blocks:
                raise ValueError(
                    "non-ok rag tool-view must not carry article_text_blocks"
                )
            if self.next_actions:
                raise ValueError(
                    "non-ok rag tool-view must not carry next_actions"
                )
        return self


# ---------------------------------------------------------------------------
# Web search tool schema (G1-b3; isolated — wired by G1-b4 agent registration)
# ---------------------------------------------------------------------------
#
# Mirrors the RAG tool-view discipline: no free-form ``payloads``, no
# provider identity, no scores/rank, no raw URLs in sidecar. Web source
# text (URL / title / description) appears only inside
# ``web_source_blocks`` as renderer-minted
# ``<untrusted_web_source role="search_web">`` XML blocks; the model
# only receives opaque ``evh_`` handles for citation.

WebSearchToolStatus = Literal[
    # Success path with at least one registered web evidence handle.
    "ok",
    # Provider explicitly returned zero results for the query.
    "empty",
    # Provider / capability not available (port None, call-limit hit,
    # fence failure, fake-empty-script). Fail-soft safe view.
    "unavailable",
    # Provider call raised or returned a malformed payload. Fail-soft.
    "failed",
]


class SearchWebToolInput(BaseModel):
    """Model-facing input for the ``search_web`` host function tool.

    The model supplies only the query (and optional result cap). All
    server-owned scope (envelope / record / base / generation / user /
    tenant / capability state) is taken from :class:`ReaderRecordAskDeps`
    and never from this input.

    ``query`` is bounded by :data:`WEB_QUERY_MAX_LEN`. The turn
    coordinator clamps / rejects over-length queries *before* calling
    the backend port. ``max_results`` is bounded by
    :data:`WEB_MAX_RESULTS_PER_CALL`; the coordinator clamps to the
    resolved capability's ``max_results_per_call`` if larger.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=WEB_QUERY_MAX_LEN)
    max_results: int | None = Field(default=None, ge=1, le=WEB_MAX_RESULTS_PER_CALL)


class SearchWebToolView(BaseModel):
    """Narrow model-visible tool-view for ``search_web``.

    Field discipline mirrors :class:`RagSearchToolView`: no free-form
    ``payloads``, no provider identity, no scores/rank, no raw URLs in
    sidecar. Web source text (canonical URL / title / description) is
    renderer-minted as ``<untrusted_web_source role="search_web">``
    XML blocks; the model only receives opaque ``evh_`` handles.

    ``status="ok"`` requires aligned 1:1 ``evidence_handles`` and
    ``web_source_blocks`` (one block per cited web source). The host
    always re-canonicalizes provider URLs before emitting the block, so
    the model never sees the raw provider URL field.

    Fail-soft safe views (``empty`` / ``unavailable`` / ``failed``)
    carry no handles, no blocks, no actions — the agent gets a typed
    status + bounded summary only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: WebSearchToolStatus
    summary: str = Field(min_length=1, max_length=400)
    next_actions: tuple[str, ...] = ()
    # Opaque server-minted evidence handles (citeable) — ok only.
    evidence_handles: tuple[EvidenceHandleRef, ...] = ()
    # Renderer-minted untrusted XML blocks — ok only; one per handle.
    # Each block carries the canonical URL, title, and optional
    # description as escaped XML text; the model never sees raw
    # provider payload or provider_result_ref.
    web_source_blocks: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_status_field_coupling(self) -> SearchWebToolView:
        if self.status == "ok":
            if not self.evidence_handles:
                raise ValueError(
                    "ok web tool-view requires evidence_handles"
                )
            if not self.web_source_blocks:
                raise ValueError(
                    "ok web tool-view requires web_source_blocks"
                )
            if len(self.evidence_handles) != len(self.web_source_blocks):
                raise ValueError(
                    "web tool-view handles and blocks must align 1:1"
                )
        else:
            # Fail-soft safe views: no handles, no blocks, no actions.
            if self.evidence_handles:
                raise ValueError(
                    "non-ok web tool-view must not carry evidence_handles"
                )
            if self.web_source_blocks:
                raise ValueError(
                    "non-ok web tool-view must not carry web_source_blocks"
                )
            if self.next_actions:
                raise ValueError(
                    "non-ok web tool-view must not carry next_actions"
                )
        return self
