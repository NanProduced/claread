from __future__ import annotations

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskResolvedIntent,
    ReaderAskSupplementCandidate,
)
from app.services.reader_ask import supplements as supplements_svc


def build_supplement_candidates(
    *,
    resolved_intent: ReaderAskResolvedIntent,
    anchors: list[ReaderAskAnchorRef],
    assistant_content_md: str,
    created_from_turn_run_id: str,
) -> list[ReaderAskSupplementCandidate]:
    if resolved_intent != "grammar" or not anchors:
        return []

    candidate = supplements_svc.build_grammar_note_candidate(
        anchor=anchors[0],
        assistant_content_md=assistant_content_md,
        created_from_turn_run_id=created_from_turn_run_id,
    )
    return [candidate] if candidate is not None else []
