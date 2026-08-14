"""Pure, fail-closed semantic-outline validator."""

from __future__ import annotations

import unicodedata

from dataclasses import dataclass
from typing import Mapping

from app.schemas.reader_orchestration import (
    ReaderSemanticOutlineDiagnostics,
    ReaderSemanticOutlineDrop,
    ReaderSemanticOutlineNode,
    ReaderSemanticOutlineStatus,
)


SemanticOutlineStatus = ReaderSemanticOutlineStatus
_MAX_DEPTH = 3
_TITLE_MAX_CODE_POINTS = 80
_ES_TRIM_EXPLICIT_CODE_POINTS = frozenset({0x0009, 0x000A, 0x000B, 0x000C, 0x000D, 0x0020, 0x00A0, 0x2028, 0x2029, 0xFEFF})


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _es_trim(value: str) -> str:
    def is_trim_character(character: str) -> bool:
        return ord(character) in _ES_TRIM_EXPLICIT_CODE_POINTS or unicodedata.category(character) == "Zs"

    start, end = 0, len(value)
    while start < end and is_trim_character(value[start]):
        start += 1
    while end > start and is_trim_character(value[end - 1]):
        end -= 1
    return value[start:end]


@dataclass(frozen=True)
class SemanticOutlineSourceIdentity:
    base_id: str
    generation: int

    @classmethod
    def from_mapping(cls, value: object) -> SemanticOutlineSourceIdentity:
        mapping = _mapping(value)
        generation = mapping.get("generation")
        return cls(
            base_id=_optional_string(mapping.get("base_id")) or "",
            generation=generation if isinstance(generation, int) and not isinstance(generation, bool) else 0,
        )


@dataclass(frozen=True)
class SemanticOutlineUnit:
    unit_id: str
    order_index: int


@dataclass(frozen=True)
class SemanticOutlineAnchor:
    anchor_segment_id: str
    unit_id: str


@dataclass(frozen=True)
class SemanticOutlineValidationContext:
    source_identity: SemanticOutlineSourceIdentity
    units: tuple[SemanticOutlineUnit, ...]
    anchors: tuple[SemanticOutlineAnchor, ...]

    @classmethod
    def from_mapping(cls, value: object) -> SemanticOutlineValidationContext:
        mapping = _mapping(value)
        raw_units = mapping.get("units")
        units = tuple(
            SemanticOutlineUnit(unit_id=unit_id, order_index=order_index)
            for item in raw_units if isinstance(raw_units, list)
            for raw in (_mapping(item),)
            for unit_id in (_optional_string(raw.get("unit_id")),)
            for order_index in (raw.get("order_index"),)
            if unit_id is not None and isinstance(order_index, int) and not isinstance(order_index, bool) and order_index >= 1
        )
        raw_anchors = mapping.get("anchors")
        anchors = tuple(
            SemanticOutlineAnchor(anchor_segment_id=anchor_id, unit_id=unit_id)
            for item in raw_anchors if isinstance(raw_anchors, list)
            for raw in (_mapping(item),)
            for anchor_id in (_optional_string(raw.get("anchor_segment_id")),)
            for unit_id in (_optional_string(raw.get("unit_id")),)
            if anchor_id is not None and unit_id is not None
        )
        return cls(SemanticOutlineSourceIdentity.from_mapping(mapping.get("source_identity")), units, anchors)


@dataclass(frozen=True)
class RawSemanticOutlineNode:
    node_id: str | None
    parent_node_id: str | None
    depth: object
    title: object
    start_unit_id: str | None
    end_unit_id: str | None
    start_anchor_segment_id: str | None
    end_anchor_segment_id: str | None

    @classmethod
    def from_mapping(cls, value: object) -> RawSemanticOutlineNode:
        raw = _mapping(value)
        return cls(
            _optional_string(raw.get("node_id")), _optional_string(raw.get("parent_node_id")), raw.get("depth"), raw.get("title"),
            _optional_string(raw.get("start_unit_id")), _optional_string(raw.get("end_unit_id")),
            _optional_string(raw.get("start_anchor_segment_id")), _optional_string(raw.get("end_anchor_segment_id")),
        )


@dataclass(frozen=True)
class SemanticOutlineValidationInput:
    field_present: bool
    requested: bool
    in_flight: bool
    worker_failure: bool
    projection_source_identity: SemanticOutlineSourceIdentity
    attempted_nodes: tuple[RawSemanticOutlineNode, ...]

    @classmethod
    def from_mapping(cls, value: object) -> SemanticOutlineValidationInput:
        mapping = _mapping(value)
        raw_nodes = mapping.get("attempted_nodes")
        nodes = tuple(RawSemanticOutlineNode.from_mapping(item) for item in raw_nodes if isinstance(raw_nodes, list))
        return cls(mapping.get("field_present") is True, mapping.get("requested") is True, mapping.get("in_flight") is True, mapping.get("worker_failure") is True, SemanticOutlineSourceIdentity.from_mapping(mapping.get("projection_source_identity")), nodes)


SemanticOutlineDrop = ReaderSemanticOutlineDrop
SemanticOutlineDiagnostics = ReaderSemanticOutlineDiagnostics
ValidatedSemanticOutlineNode = ReaderSemanticOutlineNode


@dataclass(frozen=True)
class SemanticOutlineValidationResult:
    status: SemanticOutlineStatus
    nodes: tuple[ValidatedSemanticOutlineNode, ...]
    diagnostics: SemanticOutlineDiagnostics


def validate_semantic_outline_projection(
    context: SemanticOutlineValidationContext,
    validation_input: SemanticOutlineValidationInput,
) -> SemanticOutlineValidationResult:
    """Validate candidate preorder with source fence and fail-closed tree closure."""

    def make_diagnostics(
        drops: list[SemanticOutlineDrop], skipped_node_count: int
    ) -> SemanticOutlineDiagnostics:
        return SemanticOutlineDiagnostics(
            drops=drops,
            skipped_node_count=skipped_node_count,
        )

    def terminal(
        status: SemanticOutlineStatus, reason: str | None = None
    ) -> SemanticOutlineValidationResult:
        drops = [] if reason is None else [
            SemanticOutlineDrop(node_id=None, reason_code=reason)
        ]
        return SemanticOutlineValidationResult(
            status,
            (),
            make_diagnostics(drops, 0),
        )

    attempted = validation_input.attempted_nodes
    if validation_input.worker_failure:
        return terminal("failed", "worker_failure")
    if not validation_input.field_present or (
        not validation_input.requested and not attempted
    ):
        return terminal("unavailable")
    if validation_input.in_flight and not attempted:
        return terminal("pending")
    if validation_input.projection_source_identity != context.source_identity:
        return terminal("stale", "source_mismatch")
    if not attempted:
        return terminal("failed", "empty_attempt")

    unit_order = {unit.unit_id: unit.order_index for unit in context.units}
    anchor_units = {anchor.anchor_segment_id: anchor.unit_id for anchor in context.anchors}
    accepted: list[ValidatedSemanticOutlineNode] = []
    accepted_by_id: dict[str, ValidatedSemanticOutlineNode] = {}
    dropped_ids: set[str] = set()
    seen_ids: set[str] = set()
    drops: list[SemanticOutlineDrop] = []

    def drop(
        raw: RawSemanticOutlineNode, reason: str, *, mark_dropped: bool = True
    ) -> None:
        drops.append(SemanticOutlineDrop(node_id=raw.node_id, reason_code=reason))
        if mark_dropped and raw.node_id is not None:
            dropped_ids.add(raw.node_id)

    for raw in attempted:
        if raw.node_id is None:
            drop(raw, "missing_node_id")
            continue
        if raw.node_id in seen_ids:
            # A duplicate raw candidate must not poison an already accepted parent.
            drop(raw, "duplicate_node_id", mark_dropped=False)
            continue
        seen_ids.add(raw.node_id)
        parent = raw.parent_node_id
        parent_node = accepted_by_id.get(parent) if parent is not None else None
        if parent is not None:
            if parent in dropped_ids:
                drop(raw, "parent_dropped")
                continue
            if parent_node is None:
                drop(raw, "invalid_parent")
                continue
            if raw.depth != parent_node.depth + 1:
                drop(raw, "depth_parent_mismatch")
                continue
        if (
            not isinstance(raw.depth, int)
            or isinstance(raw.depth, bool)
            or not 1 <= raw.depth <= _MAX_DEPTH
        ):
            drop(raw, "depth_out_of_range")
            continue
        if parent is None and raw.depth != 1:
            drop(raw, "invalid_root_depth")
            continue
        if not isinstance(raw.title, str):
            drop(raw, "empty_title")
            continue
        title = _es_trim(raw.title)
        if not title:
            drop(raw, "empty_title")
            continue
        if len(title) > _TITLE_MAX_CODE_POINTS:
            drop(raw, "title_too_long")
            continue
        if raw.start_unit_id not in unit_order or raw.end_unit_id not in unit_order:
            drop(raw, "missing_unit")
            continue
        start_order = unit_order[raw.start_unit_id]
        end_order = unit_order[raw.end_unit_id]
        if start_order > end_order:
            drop(raw, "inverted_range")
            continue
        if parent_node is not None:
            parent_start = unit_order[parent_node.start_unit_id]
            parent_end = unit_order[parent_node.end_unit_id]
            if start_order < parent_start or end_order > parent_end:
                drop(raw, "range_not_nested")
                continue
        if any(
            existing.parent_node_id == parent
            and not (
                end_order < unit_order[existing.start_unit_id]
                or start_order > unit_order[existing.end_unit_id]
            )
            for existing in accepted
        ):
            drop(raw, "range_overlap")
            continue
        start_anchor = raw.start_anchor_segment_id
        end_anchor = raw.end_anchor_segment_id
        invalid_anchor = False
        if (
            start_anchor is not None
            and anchor_units.get(start_anchor) != raw.start_unit_id
        ):
            start_anchor = None
            invalid_anchor = True
        if end_anchor is not None and anchor_units.get(end_anchor) != raw.end_unit_id:
            end_anchor = None
            invalid_anchor = True
        if invalid_anchor:
            drops.append(
                SemanticOutlineDrop(node_id=raw.node_id, reason_code="invalid_anchor")
            )
        node = ValidatedSemanticOutlineNode(
            node_id=raw.node_id,
            parent_node_id=parent,
            depth=raw.depth,
            title=title,
            start_unit_id=raw.start_unit_id,
            end_unit_id=raw.end_unit_id,
            start_anchor_segment_id=start_anchor,
            end_anchor_segment_id=end_anchor,
            order_index=len(accepted) + 1,
        )
        accepted.append(node)
        accepted_by_id[node.node_id] = node

    valid_count = len(accepted)
    attempted_count = len(attempted)
    status: SemanticOutlineStatus
    if attempted_count == 0 or valid_count == 0:
        status = "failed"
    elif valid_count == attempted_count:
        status = "ready"
    else:
        status = "partial"
    return SemanticOutlineValidationResult(
        status,
        tuple(accepted),
        make_diagnostics(drops, attempted_count - valid_count),
    )
