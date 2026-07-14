"""Register the turn's initial selection as server-side evidence.

Zero-tool answers that revolve around the current selection must still enter
the unified evidence set so a future finalizer can resolve handles without
relying on prompt text alone.
"""

from __future__ import annotations

from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)
from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    build_server_evidence_observation,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry


def register_initial_anchor_evidence(
    *,
    envelope: ReadingRecordAskContextEnvelope,
    registry: EvidenceRegistry,
) -> EvidenceHandleRef | None:
    """Mint an ``initial_anchor`` observation when the envelope has a selection.

    Returns the handle ref, or ``None`` when there is no initial anchor.
    Raises if the registry is bound to a different envelope fingerprint.
    """
    if registry.envelope_fingerprint != envelope.envelope_fingerprint:
        raise ValueError(
            "evidence registry fingerprint does not match envelope fingerprint"
        )
    anchor = envelope.initial_anchor
    if anchor is None:
        return None

    snippet = anchor.selected_text
    if len(snippet) > 2000:
        snippet = snippet[:2000]

    observation = build_server_evidence_observation(
        kind="initial_anchor",
        envelope_fingerprint=envelope.envelope_fingerprint,
        source_tool="initial_anchor",
        snippet=snippet,
        locator_summary={
            "mode": "initial_anchor",
            "unit_id": anchor.unit_id,
            "anchor_segment_id": anchor.anchor_segment_id,
            "offset_unit": anchor.offset_unit,
            "start_offset": anchor.start_offset,
            "end_offset": anchor.end_offset,
            "text_hash": anchor.text_hash,
            "untrusted": True,
        },
        unit_id=anchor.unit_id,
        anchor_segment_id=anchor.anchor_segment_id,
    )
    return registry.register(observation)
