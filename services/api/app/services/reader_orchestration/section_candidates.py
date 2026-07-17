"""T5.6a — project SectionCandidate[] from a trusted outline (pure)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .section_identity import (
    SectionIdentity,
    SectionIdentityError,
    SectionUnit,
    try_build_section_identity,
)

_TRUSTED_OUTLINE_STATUSES = frozenset({"ready", "partial"})


@dataclass(frozen=True, slots=True)
class OutlineNodeInput:
    node_id: str
    start_unit_id: str
    end_unit_id: str
    title: str = ""
    order_index: int = 0
    start_anchor_segment_id: str | None = None
    end_anchor_segment_id: str | None = None


@dataclass(frozen=True, slots=True)
class TrustedOutlineInput:
    status: str
    source_base_id: str
    source_generation: int
    outline_revision: str | None
    nodes: tuple[OutlineNodeInput, ...]


@dataclass(frozen=True, slots=True)
class SectionCandidate:
    identity: SectionIdentity
    """Audit-only — never durable identity / target key / fingerprint primary."""
    audit_node_id: str | None
    audit_outline_revision: str | None
    title: str
    order_index: int


def project_section_candidates_from_outline(
    *,
    record_id: str,
    base_id: str,
    generation: int,
    outline: TrustedOutlineInput | None,
    ordered_units: Sequence[SectionUnit],
    anchor_to_unit: Mapping[str, str] | None = None,
) -> tuple[SectionCandidate, ...]:
    """Return stable-order candidates or empty on any fail-closed condition.

    Rules (R1 / T5.6a):
    - only ready|partial + matching source identity
    - **any** node with invalid range → zero candidates (full fail-closed)
    - same geometric range de-duplicated (first wins); nested ranges not merged
    - node_id / outline_revision are audit metadata only
    """
    if outline is None:
        return ()
    if outline.status not in _TRUSTED_OUTLINE_STATUSES:
        return ()
    if not outline.nodes:
        return ()
    if outline.source_base_id != base_id or outline.source_generation != generation:
        return ()

    anchors = anchor_to_unit or {}
    built: list[SectionCandidate] = []
    seen_geometry: set[tuple[str, str, str | None, str | None]] = set()

    for node in outline.nodes:
        try:
            identity = try_build_section_identity(
                record_id=record_id,
                base_id=base_id,
                generation=generation,
                start_unit_id=node.start_unit_id,
                end_unit_id=node.end_unit_id,
                ordered_units=ordered_units,
                start_anchor_segment_id=node.start_anchor_segment_id,
                end_anchor_segment_id=node.end_anchor_segment_id,
                anchor_to_unit=anchors,
            )
        except SectionIdentityError:
            # Full fail-closed: one bad node voids the candidate list.
            return ()

        geo = identity.geometric_key()
        if geo in seen_geometry:
            continue
        seen_geometry.add(geo)
        built.append(
            SectionCandidate(
                identity=identity,
                audit_node_id=node.node_id or None,
                audit_outline_revision=outline.outline_revision,
                title=node.title,
                order_index=node.order_index,
            )
        )

    # Stable order: input node order among accepted unique geometries.
    return tuple(built)


__all__ = [
    "OutlineNodeInput",
    "SectionCandidate",
    "TrustedOutlineInput",
    "project_section_candidates_from_outline",
]
