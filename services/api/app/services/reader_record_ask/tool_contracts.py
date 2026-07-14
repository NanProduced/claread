"""Reading Record Ask — schema-first contracts for first-wave read tools.

Tools covered (schema only; **no executors** in this slice):

- ``read_range``
- ``search_current_article``

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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.reader_record_ask.context_envelope import SERVER_OWNED_SCOPE_FIELDS
from app.services.reader_record_ask.evidence import EvidenceHandleRef

# ---------------------------------------------------------------------------
# Tool names
# ---------------------------------------------------------------------------

TOOL_READ_RANGE: Literal["read_range"] = "read_range"
TOOL_SEARCH_CURRENT_ARTICLE: Literal["search_current_article"] = (
    "search_current_article"
)

ReaderRecordAskReadToolName = Literal["read_range", "search_current_article"]

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
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    limit: int | None = Field(default=None, ge=1, le=20)


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
