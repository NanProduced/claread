"""Build Context Envelope + document scope from already-loaded snapshot facts."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.services.reader_record_ask.context_envelope import (
    EnvelopeInitialAnchor,
    ReadingRecordAskContextEnvelope,
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.document_access import (
    AnchorSegmentView,
    DocumentScopeSnapshot,
    InMemoryDocumentAccess,
    ReadingUnitView,
    build_document_scope,
)


def build_envelope_from_facts(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    facts: Any,
    request_anchor: Any | None,
    validated_anchor: Any | None = None,
    stable_document_id: UUID | None = None,
) -> ReadingRecordAskContextEnvelope:
    """Construct envelope after route-level record/anchor validation."""
    base = facts.build_result.base
    record = facts.record
    initial: EnvelopeInitialAnchor | None = None
    if request_anchor is not None:
        base_start = None
        base_end = None
        if validated_anchor is not None:
            seg = getattr(validated_anchor, "anchor_segment", None)
            if seg is not None:
                base_start = int(seg.base_start_utf16) + int(request_anchor.start_offset)
                base_end = int(seg.base_start_utf16) + int(request_anchor.end_offset)
        initial = EnvelopeInitialAnchor(
            unit_id=str(request_anchor.unit_id),
            anchor_segment_id=str(request_anchor.anchor_segment_id),
            start_offset=int(request_anchor.start_offset),
            end_offset=int(request_anchor.end_offset),
            selected_text=str(request_anchor.selected_text),
            text_hash=str(request_anchor.text_hash),
            base_start_utf16=base_start,
            base_end_utf16=base_end,
        )
    return build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=user_id,
            reading_record_id=reading_record_id,
            base_id=UUID(str(base.base_id)),
            record_generation=int(record.generation),
            stable_document_id=stable_document_id,
            base_content_sha256=str(base.content_sha256),
            product_state=str(record.product_state),
            readiness_state=str(record.readiness_state),
            initial_anchor=initial,
            visible_range=None,
            can_read_range=True,
            can_search_current_article=True,
            article_rag_ready=False,
        )
    )


def document_access_from_facts(
    *,
    reading_record_id: UUID,
    facts: Any,
    stable_document_id: UUID | None = None,
) -> InMemoryDocumentAccess:
    """Materialize an in-memory document scope from snapshot units/segments."""
    base = facts.build_result.base
    units = tuple(
        ReadingUnitView(
            unit_id=u.unit_id,
            order_index=int(u.order_index),
            text=u.text,
            text_hash=u.text_hash,
            base_start_utf16=int(u.base_start_utf16),
            base_end_utf16=int(u.base_end_utf16),
        )
        for u in facts.build_result.units
    )
    segments = tuple(
        AnchorSegmentView(
            unit_id=s.unit_id,
            anchor_segment_id=s.anchor_segment_id,
            order_index=int(s.order_index),
            unit_order_index=int(s.unit_order_index),
            text=s.text,
            text_hash=s.text_hash,
            unit_start_utf16=int(s.unit_start_utf16),
            unit_end_utf16=int(s.unit_end_utf16),
            base_start_utf16=int(s.base_start_utf16),
            base_end_utf16=int(s.base_end_utf16),
        )
        for s in facts.build_result.anchor_segments
    )
    scope: DocumentScopeSnapshot = build_document_scope(
        reading_record_id=reading_record_id,
        base_id=UUID(str(base.base_id)),
        record_generation=int(facts.record.generation),
        units=units,
        segments=segments,
        stable_document_id=stable_document_id,
        base_content_sha256=str(base.content_sha256),
    )
    return InMemoryDocumentAccess(snapshot=scope)
