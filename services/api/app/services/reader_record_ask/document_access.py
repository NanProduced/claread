"""Narrow document-access seam for Reading Record Ask read tools.

Provides unit/segment text for the *current* envelope scope only.
Does not import legacy ``reader_ask`` context runtime or Ask planner.

Snapshots carry record / base / generation / stable-document identity so
the executor can reject a DocumentAccess implementation that returns the
wrong article under a matching generation alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)


class ReadingUnitView(BaseModel):
    """Unit text view within one envelope-scoped document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_id: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    text: str
    text_hash: str = Field(min_length=1)
    base_start_utf16: int = Field(ge=0)
    base_end_utf16: int = Field(gt=0)


class AnchorSegmentView(BaseModel):
    """Anchor segment view within one envelope-scoped document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_id: str = Field(min_length=1)
    anchor_segment_id: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    unit_order_index: int = Field(ge=0)
    text: str
    text_hash: str = Field(min_length=1)
    unit_start_utf16: int = Field(ge=0)
    unit_end_utf16: int = Field(gt=0)
    base_start_utf16: int = Field(ge=0)
    base_end_utf16: int = Field(gt=0)


class DocumentScopeSnapshot(BaseModel):
    """Immutable unit/segment snapshot bound to envelope identity fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Identity — must match the turn envelope before any text is used.
    reading_record_id: UUID
    base_id: UUID
    record_generation: int = Field(ge=1)
    # Present only when the active Stable Reading Document is known.
    stable_document_id: UUID | None = None
    # Content hash of the active base when available (fence material).
    base_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    units: tuple[ReadingUnitView, ...] = ()
    segments: tuple[AnchorSegmentView, ...] = ()

    def unit_by_id(self, unit_id: str) -> ReadingUnitView | None:
        for unit in self.units:
            if unit.unit_id == unit_id:
                return unit
        return None

    def segment_by_id(self, anchor_segment_id: str) -> AnchorSegmentView | None:
        for segment in self.segments:
            if segment.anchor_segment_id == anchor_segment_id:
                return segment
        return None

    def units_by_order_span(
        self,
        start_order: int,
        end_order: int,
    ) -> tuple[ReadingUnitView, ...]:
        return tuple(
            unit
            for unit in sorted(self.units, key=lambda item: item.order_index)
            if start_order <= unit.order_index <= end_order
        )


def scope_identity_mismatch_reason(
    scope: DocumentScopeSnapshot,
    envelope: ReadingRecordAskContextEnvelope,
) -> str | None:
    """Return a human-readable mismatch reason, or ``None`` when scope matches.

    Checks record, base, generation, stable document (when present on either
    side), and base content hash (when the envelope carries one).
    """
    if scope.reading_record_id != envelope.reading_record_id:
        return (
            f"scope reading_record_id {scope.reading_record_id} does not match "
            f"envelope {envelope.reading_record_id}"
        )
    if scope.base_id != envelope.base_id:
        return (
            f"scope base_id {scope.base_id} does not match envelope {envelope.base_id}"
        )
    if scope.record_generation != envelope.record_generation:
        return (
            f"scope generation {scope.record_generation} does not match "
            f"envelope generation {envelope.record_generation}"
        )
    if (
        envelope.stable_document_id is not None
        and scope.stable_document_id != envelope.stable_document_id
    ):
        return (
            f"scope stable_document_id {scope.stable_document_id} does not match "
            f"envelope {envelope.stable_document_id}"
        )
    if (
        scope.stable_document_id is not None
        and envelope.stable_document_id is not None
        and scope.stable_document_id != envelope.stable_document_id
    ):
        return "scope and envelope stable_document_id diverge"
    # Envelope hash is authoritative when present; missing scope hash cannot
    # satisfy a hashed envelope.
    if envelope.base_content_sha256 is not None:
        if scope.base_content_sha256 is None:
            return "envelope requires base_content_sha256 but scope has none"
        if scope.base_content_sha256 != envelope.base_content_sha256:
            return "scope base_content_sha256 does not match envelope"
    return None


class DocumentAccess(Protocol):
    """Protocol for loading the current record/base/generation document scope.

    Implementations must honour the envelope identity passed in and must not
    return units from another record/base/generation.
    """

    async def load_document_scope(
        self,
        *,
        user_id: UUID,
        reading_record_id: UUID,
        base_id: UUID,
        record_generation: int,
    ) -> DocumentScopeSnapshot:
        """Load units/segments for the envelope scope.

        Raises
        ------
        LookupError
            When the scope is missing or no longer matches the requested
            record/base/generation (caller maps to typed tool status).
        """


@dataclass(slots=True)
class InMemoryDocumentAccess:
    """Test / scripted document access with a fixed scope snapshot.

    Records load attempts so budget/stale tests can assert no I/O.
    Validates request identity against the snapshot before returning it.
    """

    snapshot: DocumentScopeSnapshot
    load_count: int = 0
    # When set, the next load raises LookupError (simulates missing scope).
    raise_missing: bool = False

    async def load_document_scope(
        self,
        *,
        user_id: UUID,
        reading_record_id: UUID,
        base_id: UUID,
        record_generation: int,
    ) -> DocumentScopeSnapshot:
        del user_id
        self.load_count += 1
        if self.raise_missing:
            raise LookupError("document scope not found for envelope")
        if self.snapshot.reading_record_id != reading_record_id:
            raise LookupError(
                "document scope reading_record_id does not match request"
            )
        if self.snapshot.base_id != base_id:
            raise LookupError("document scope base_id does not match request")
        if self.snapshot.record_generation != record_generation:
            raise LookupError(
                "document scope generation does not match envelope generation"
            )
        return self.snapshot


def build_document_scope(
    *,
    reading_record_id: UUID,
    base_id: UUID,
    record_generation: int,
    units: Sequence[ReadingUnitView],
    segments: Sequence[AnchorSegmentView] = (),
    stable_document_id: UUID | None = None,
    base_content_sha256: str | None = None,
) -> DocumentScopeSnapshot:
    """Pure builder for a document scope snapshot (no DB)."""
    return DocumentScopeSnapshot(
        reading_record_id=reading_record_id,
        base_id=base_id,
        record_generation=record_generation,
        stable_document_id=stable_document_id,
        base_content_sha256=base_content_sha256,
        units=tuple(units),
        segments=tuple(segments),
    )
