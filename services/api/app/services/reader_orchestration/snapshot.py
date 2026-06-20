from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime

from app.contracts.annotation import slice_by_utf16_offsets
from app.schemas.reader_orchestration import (
    ReaderPlateSnapshot,
    ReaderSnapshotAskSupplement,
    ReaderSnapshotBase,
    ReaderSnapshotLayer,
    ReaderSnapshotNavigation,
    ReaderSnapshotNavigationUnit,
    ReaderSnapshotParsedDecision,
    ReaderSnapshotUserAsset,
    ReaderTextRangeAnchor,
    ReaderUnitAnchor,
    TranslationLayerOutput,
)

from .base_builder import (
    BuiltAnchorSegment,
    BuiltReadingUnit,
    ReadingBaseBuildResult,
    result_length_utf16,
)


def build_reader_plate_snapshot(
    build_result: ReadingBaseBuildResult,
    *,
    snapshot_taken_at: datetime,
    last_event_sequence: int,
    enhancement_layers: Sequence[ReaderSnapshotLayer] | None = None,
    ask_supplements: Sequence[ReaderSnapshotAskSupplement] | None = None,
    user_assets: Sequence[ReaderSnapshotUserAsset] | None = None,
    parsed_decisions: Sequence[ReaderSnapshotParsedDecision] | None = None,
    snapshot_id: str | None = None,
) -> ReaderPlateSnapshot:
    _validate_snapshot_inputs(
        build_result,
        enhancement_layers or [],
        ask_supplements or [],
        user_assets or [],
        parsed_decisions or [],
    )
    layers = _sort_layers(enhancement_layers or [])
    supplements = sorted(
        ask_supplements or [],
        key=lambda item: (item.created_at, item.supplement_id),
    )
    assets = sorted(
        user_assets or [],
        key=lambda item: (item.updated_at, item.asset_id),
    )
    decisions = _sort_parsed_decisions(build_result, parsed_decisions or [])

    return ReaderPlateSnapshot(
        snapshot_id=(
            snapshot_id
            or _build_snapshot_id(build_result, last_event_sequence, layers, decisions)
        ),
        snapshot_taken_at=snapshot_taken_at,
        last_event_sequence=last_event_sequence,
        record_id=build_result.base.reading_record_id,
        base=ReaderSnapshotBase(
            base_id=build_result.base.base_id,
            content_sha256=build_result.base.content_sha256,
            canonicalizer_version=build_result.base.canonicalizer_version,
            builder_version=build_result.base.builder_version,
            segmenter_version=build_result.base.segmenter_version,
            text_length_utf16=build_result.base.content_utf16_length,
        ),
        navigation=ReaderSnapshotNavigation(
            units=[
                ReaderSnapshotNavigationUnit(
                    unit_id=unit.unit_id,
                    order_index=unit.order_index,
                    unit_type=unit.unit_type,
                    boundary_quality=unit.boundary_quality,
                    label=unit.label,
                    base_start_utf16=unit.base_start_utf16,
                    base_end_utf16=unit.base_end_utf16,
                )
                for unit in build_result.navigation_units
            ]
        ),
        enhancement_layers=list(layers),
        ask_supplements=list(supplements),
        user_assets=list(assets),
        parsed_decisions=list(decisions),
        value=_build_plate_value(build_result, layers),
    )


def _validate_snapshot_inputs(
    build_result: ReadingBaseBuildResult,
    enhancement_layers: Sequence[ReaderSnapshotLayer],
    ask_supplements: Sequence[ReaderSnapshotAskSupplement],
    user_assets: Sequence[ReaderSnapshotUserAsset],
    parsed_decisions: Sequence[ReaderSnapshotParsedDecision],
) -> None:
    base_id = build_result.base.base_id
    unit_ids = {unit.unit_id for unit in build_result.units}
    anchor_segment_to_unit = {
        segment.anchor_segment_id: segment.unit_id for segment in build_result.anchor_segments
    }

    for layer in enhancement_layers:
        if layer.base_id != base_id:
            raise ValueError(
                f"snapshot layer {layer.layer_id} base_id must match current base {base_id}"
            )
        _validate_layer_target(layer, unit_ids, anchor_segment_to_unit)

    for supplement in ask_supplements:
        if supplement.anchor is None:
            continue
        _validate_snapshot_anchor(
            supplement.anchor,
            base_id,
            unit_ids,
            anchor_segment_to_unit,
            context=f"ask supplement {supplement.supplement_id}",
        )

    for asset in user_assets:
        _validate_snapshot_anchor(
            asset.anchor,
            base_id,
            unit_ids,
            anchor_segment_to_unit,
            context=f"user asset {asset.asset_id}",
        )

    for decision in parsed_decisions:
        if decision.unit_id not in unit_ids:
            raise ValueError(
                "parsed decision unit_id "
                f"{decision.unit_id} must exist in the current snapshot base"
            )


def _validate_layer_target(
    layer: ReaderSnapshotLayer,
    unit_ids: set[str],
    anchor_segment_to_unit: dict[str, str],
) -> None:
    if layer.target_scope == "unit":
        if layer.target_key not in unit_ids:
            raise ValueError(
                f"snapshot layer {layer.layer_id} target unit {layer.target_key} does not exist"
            )
        return

    if layer.target_scope == "anchor_segment":
        if layer.target_key not in anchor_segment_to_unit:
            raise ValueError(
                "snapshot layer "
                f"{layer.layer_id} target anchor segment {layer.target_key} does not exist"
            )
        return

    if layer.layer_type == "translation" and layer.target_scope not in {"unit", "anchor_segment"}:
        raise ValueError(
            "translation snapshot layer "
            f"{layer.layer_id} must target a unit or anchor segment in D4"
        )


def _validate_snapshot_anchor(
    anchor: ReaderUnitAnchor | ReaderTextRangeAnchor,
    expected_base_id: str,
    unit_ids: set[str],
    anchor_segment_to_unit: dict[str, str],
    *,
    context: str,
) -> None:
    if anchor.base_id != expected_base_id:
        raise ValueError(f"{context} anchor base_id must match current base {expected_base_id}")
    if anchor.unit_id not in unit_ids:
        raise ValueError(f"{context} anchor unit_id {anchor.unit_id} does not exist")

    if isinstance(anchor, ReaderTextRangeAnchor):
        anchor_unit_id = anchor_segment_to_unit.get(anchor.anchor_segment_id)
        if anchor_unit_id is None:
            raise ValueError(
                f"{context} anchor_segment_id {anchor.anchor_segment_id} does not exist"
            )
        if anchor_unit_id != anchor.unit_id:
            raise ValueError(
                f"{context} anchor segment {anchor.anchor_segment_id} "
                f"does not belong to {anchor.unit_id}"
            )


def _build_snapshot_id(
    build_result: ReadingBaseBuildResult,
    last_event_sequence: int,
    enhancement_layers: Sequence[ReaderSnapshotLayer],
    parsed_decisions: Sequence[ReaderSnapshotParsedDecision],
) -> str:
    parts = [
        build_result.base.reading_record_id,
        build_result.base.base_id,
        build_result.base.content_sha256,
        str(last_event_sequence),
    ]
    parts.extend(
        f"layer:{layer.layer_id}:{layer.target_scope}:{layer.target_key}:{layer.schema_version}"
        for layer in enhancement_layers
    )
    parts.extend(
        "parsed:"
        f"{decision.unit_id}:{decision.policy_code}:{decision.parsed_state}:"
        f"{decision.rationale_code or ''}"
        for decision in parsed_decisions
    )
    fingerprint = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"reader_snapshot_{fingerprint}"


def _build_plate_value(
    build_result: ReadingBaseBuildResult,
    enhancement_layers: Sequence[ReaderSnapshotLayer],
) -> list[dict[str, object]]:
    segments_by_unit = _group_segments(build_result.anchor_segments)
    translation_layers_by_target = _group_translation_layers(enhancement_layers)
    value: list[dict[str, object]] = []

    for unit in build_result.units:
        unit_segments = segments_by_unit[unit.unit_id]
        unit_children: list[dict[str, object]] = [_build_source_block(unit, unit_segments)]
        unit_children.extend(
            _build_translation_nodes(
                unit,
                translation_layers_by_target,
                unit_segments,
            )
        )

        value.append(
            {
                "type": "reader_unit",
                "owner": "stable",
                "base_id": unit.base_id,
                "unit_id": unit.unit_id,
                "order_index": unit.order_index,
                "unit_type": unit.unit_type,
                "boundary_quality": unit.boundary_quality,
                "base_start_utf16": unit.base_start_utf16,
                "base_end_utf16": unit.base_end_utf16,
                "text_hash": unit.text_hash,
                "hash_algorithm": "fnv1a32-utf16",
                "children": unit_children,
            }
        )
    return value


def _build_source_block(
    unit: BuiltReadingUnit,
    unit_segments: Sequence[BuiltAnchorSegment],
) -> dict[str, object]:
    children: list[dict[str, object]] = []
    cursor = 0
    unit_utf16_length = result_length_utf16(unit.text)

    for segment in unit_segments:
        if segment.unit_start_utf16 > cursor:
            separator_text = slice_by_utf16_offsets(unit.text, cursor, segment.unit_start_utf16)
            if separator_text is None:
                raise ValueError(f"unit {unit.unit_id} has an invalid separator slice")
            children.append(
                _build_stable_leaf(
                    text=separator_text,
                    source_role="separator",
                    base_start_utf16=unit.base_start_utf16 + cursor,
                    base_end_utf16=unit.base_start_utf16 + segment.unit_start_utf16,
                )
            )

        segment_text = slice_by_utf16_offsets(
            unit.text,
            segment.unit_start_utf16,
            segment.unit_end_utf16,
        )
        if segment_text != segment.text:
            raise ValueError(
                f"unit {unit.unit_id} anchor {segment.anchor_segment_id} does not round-trip"
            )

        children.append(
            {
                "type": "reader_anchor_segment",
                "owner": "stable",
                "base_id": unit.base_id,
                "unit_id": unit.unit_id,
                "anchor_segment_id": segment.anchor_segment_id,
                "sentence_id": segment.sentence_id,
                "segment_type": segment.segment_type,
                "boundary_quality": segment.boundary_quality,
                "base_start_utf16": segment.base_start_utf16,
                "base_end_utf16": segment.base_end_utf16,
                "unit_start_utf16": segment.unit_start_utf16,
                "unit_end_utf16": segment.unit_end_utf16,
                "text_hash": segment.text_hash,
                "hash_algorithm": "fnv1a32-utf16",
                "children": [
                    _build_stable_leaf(
                        text=segment.text,
                        source_role="segment_text",
                        base_start_utf16=segment.base_start_utf16,
                        base_end_utf16=segment.base_end_utf16,
                        anchor_segment_id=segment.anchor_segment_id,
                        segment_start_utf16=0,
                        segment_end_utf16=segment.unit_end_utf16 - segment.unit_start_utf16,
                    )
                ],
            }
        )
        cursor = segment.unit_end_utf16

    if cursor < unit_utf16_length:
        trailing_text = slice_by_utf16_offsets(unit.text, cursor, unit_utf16_length)
        if trailing_text is None:
            raise ValueError(f"unit {unit.unit_id} has an invalid trailing separator slice")
        children.append(
            _build_stable_leaf(
                text=trailing_text,
                source_role="separator",
                base_start_utf16=unit.base_start_utf16 + cursor,
                base_end_utf16=unit.base_end_utf16,
            )
        )

    rebuilt_text = _collect_stable_text(children)
    if rebuilt_text != unit.text:
        raise ValueError(f"unit {unit.unit_id} source leaves do not rebuild stable base text")

    return {
        "type": "reader_source_block",
        "owner": "stable",
        "base_id": unit.base_id,
        "unit_id": unit.unit_id,
        "base_start_utf16": unit.base_start_utf16,
        "base_end_utf16": unit.base_end_utf16,
        "children": children,
    }


def _build_stable_leaf(
    *,
    text: str,
    source_role: str,
    base_start_utf16: int,
    base_end_utf16: int,
    anchor_segment_id: str | None = None,
    segment_start_utf16: int | None = None,
    segment_end_utf16: int | None = None,
) -> dict[str, object]:
    leaf: dict[str, object] = {
        "text": text,
        "owner": "stable",
        "lock_source": True,
        "source_role": source_role,
        "base_start_utf16": base_start_utf16,
        "base_end_utf16": base_end_utf16,
    }
    if anchor_segment_id is not None:
        leaf["anchor_segment_id"] = anchor_segment_id
    if segment_start_utf16 is not None:
        leaf["segment_start_utf16"] = segment_start_utf16
    if segment_end_utf16 is not None:
        leaf["segment_end_utf16"] = segment_end_utf16
    return leaf


def _group_segments(
    anchor_segments: Sequence[BuiltAnchorSegment],
) -> dict[str, list[BuiltAnchorSegment]]:
    grouped: dict[str, list[BuiltAnchorSegment]] = {}
    for segment in anchor_segments:
        grouped.setdefault(segment.unit_id, []).append(segment)
    for segments in grouped.values():
        segments.sort(key=lambda item: item.unit_order_index)
    return grouped


def _sort_layers(layers: Iterable[ReaderSnapshotLayer]) -> list[ReaderSnapshotLayer]:
    return sorted(
        layers,
        key=lambda layer: (
            layer.layer_type,
            layer.target_scope,
            layer.target_key,
            layer.published_at,
            layer.layer_id,
        ),
    )


def _sort_parsed_decisions(
    build_result: ReadingBaseBuildResult,
    parsed_decisions: Sequence[ReaderSnapshotParsedDecision],
) -> list[ReaderSnapshotParsedDecision]:
    unit_order = {unit.unit_id: unit.order_index for unit in build_result.units}
    return sorted(
        parsed_decisions,
        key=lambda decision: (
            unit_order.get(decision.unit_id, 10**9),
            decision.policy_code,
        ),
    )


def _collect_stable_text(nodes: Sequence[dict[str, object]]) -> str:
    parts: list[str] = []
    for node in nodes:
        if "text" in node:
            text = node.get("text")
            if isinstance(text, str):
                parts.append(text)
            continue
        children = node.get("children")
        if isinstance(children, list):
            parts.append(
                _collect_stable_text(
                    [child for child in children if isinstance(child, dict)]
                )
            )
    return "".join(parts)


def _group_translation_layers(
    layers: Sequence[ReaderSnapshotLayer],
) -> dict[tuple[str, str], list[ReaderSnapshotLayer]]:
    grouped: dict[tuple[str, str], list[ReaderSnapshotLayer]] = {}
    for layer in layers:
        if layer.layer_type != "translation":
            continue
        grouped.setdefault((layer.target_scope, layer.target_key), []).append(layer)
    return grouped


def _build_translation_nodes(
    unit: BuiltReadingUnit,
    translation_layers_by_target: dict[tuple[str, str], list[ReaderSnapshotLayer]],
    unit_segments: Sequence[BuiltAnchorSegment],
) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
    nodes.extend(
        _build_translation_nodes_for_layers(
            unit=unit,
            anchor_segment_id=None,
            layers=translation_layers_by_target.get(("unit", unit.unit_id), []),
        )
    )
    for segment in unit_segments:
        nodes.extend(
            _build_translation_nodes_for_layers(
                unit=unit,
                anchor_segment_id=segment.anchor_segment_id,
                layers=translation_layers_by_target.get(
                    ("anchor_segment", segment.anchor_segment_id),
                    [],
                ),
            )
        )
    return nodes


def _build_translation_nodes_for_layers(
    *,
    unit: BuiltReadingUnit,
    anchor_segment_id: str | None,
    layers: Sequence[ReaderSnapshotLayer],
) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
    for layer in layers:
        output = TranslationLayerOutput.model_validate(layer.output)
        node: dict[str, object] = {
            "type": "reader_translation",
            "owner": "system_ai",
            "layer_id": layer.layer_id,
            "layer_version": layer.schema_version,
            "base_id": unit.base_id,
            "unit_id": unit.unit_id,
            "target_scope": layer.target_scope,
            "target_key": layer.target_key,
            "target_language": output.target_language,
            "confidence": output.confidence,
            "notes": output.notes,
            "children": [{"text": output.translated_text}],
        }
        if anchor_segment_id is not None:
            node["anchor_segment_id"] = anchor_segment_id
        nodes.append(node)
    return nodes
