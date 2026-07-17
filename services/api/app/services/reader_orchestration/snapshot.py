from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime

from pydantic import ValidationError

from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.schemas.reader_orchestration import (
    GrammarNoteLayerOutput,
    ReaderEnhancementProgress,
    ReaderEnhancementProgressLayer,
    ReaderPlateSnapshot,
    ReaderSnapshotAnchorSegment,
    ReaderSnapshotAskSupplement,
    ReaderSnapshotBase,
    ReaderSnapshotLayer,
    ReaderSnapshotNavigation,
    ReaderSnapshotNavigationUnit,
    ReaderSnapshotParsedDecision,
    ReaderSnapshotRecord,
    ReaderSnapshotUserAsset,
    ReaderTextRangeAnchor,
    ReaderUnitAnchor,
    SentenceAnalysisLayerOutput,
    TranslationLayerOutput,
    VocabularyLayerOutput,
)

from .base_builder import (
    BuiltAnchorSegment,
    BuiltReadingUnit,
    ReadingBaseBuildResult,
    result_length_utf16,
)
from .grammar_layer_payload import (
    build_grammar_item_id,
    get_grammar_item_terminal_span_index,
)
from .semantic_outline_snapshot import project_semantic_outline_for_snapshot


def build_reader_plate_snapshot(
    build_result: ReadingBaseBuildResult,
    *,
    snapshot_taken_at: datetime,
    last_event_sequence: int,
    record: ReaderSnapshotRecord | None = None,
    enhancement_layers: Sequence[ReaderSnapshotLayer] | None = None,
    ask_supplements: Sequence[ReaderSnapshotAskSupplement] | None = None,
    user_assets: Sequence[ReaderSnapshotUserAsset] | None = None,
    parsed_decisions: Sequence[ReaderSnapshotParsedDecision] | None = None,
    enhancement_progress: ReaderEnhancementProgress | None = None,
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
    units_by_id = {unit.unit_id: unit for unit in build_result.units}
    snapshot_record = record or _build_default_snapshot_record(
        build_result, snapshot_taken_at
    )
    semantic_outline = project_semantic_outline_for_snapshot(
        build_result=build_result,
        record_generation=snapshot_record.generation,
        enhancement_layers=layers,
    )

    return ReaderPlateSnapshot(
        snapshot_id=(
            snapshot_id
            or _build_snapshot_id(build_result, last_event_sequence, layers, decisions)
        ),
        snapshot_taken_at=snapshot_taken_at,
        last_event_sequence=last_event_sequence,
        record_id=build_result.base.reading_record_id,
        record=snapshot_record,
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
                    unit_id=navigation_unit.unit_id,
                    order_index=navigation_unit.order_index,
                    unit_type=navigation_unit.unit_type,
                    boundary_quality=navigation_unit.boundary_quality,
                    label=navigation_unit.label,
                    base_start_utf16=navigation_unit.base_start_utf16,
                    base_end_utf16=navigation_unit.base_end_utf16,
                    text_hash=units_by_id[navigation_unit.unit_id].text_hash,
                )
                for navigation_unit in build_result.navigation_units
            ]
        ),
        anchor_segments=[
            ReaderSnapshotAnchorSegment(
                anchor_segment_id=segment.anchor_segment_id,
                sentence_id=segment.sentence_id,
                paragraph_id=segment.paragraph_id,
                unit_id=segment.unit_id,
                order_index=segment.order_index,
                unit_order_index=segment.unit_order_index,
                segment_type=segment.segment_type,
                boundary_quality=segment.boundary_quality,
                base_start_utf16=segment.base_start_utf16,
                base_end_utf16=segment.base_end_utf16,
                unit_start_utf16=segment.unit_start_utf16,
                unit_end_utf16=segment.unit_end_utf16,
                text_hash=segment.text_hash,
            )
            for segment in build_result.anchor_segments
        ],
        enhancement_layers=list(layers),
        enhancement_progress=enhancement_progress
        or _build_default_enhancement_progress(layers),
        ask_supplements=list(supplements),
        user_assets=list(assets),
        parsed_decisions=list(decisions),
        value=_build_plate_value(build_result, layers),
        semantic_outline=semantic_outline,
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
    units_by_id = {unit.unit_id: unit for unit in build_result.units}
    anchor_segment_to_unit = {
        segment.anchor_segment_id: segment.unit_id for segment in build_result.anchor_segments
    }
    anchor_segments_by_id = {
        segment.anchor_segment_id: segment for segment in build_result.anchor_segments
    }

    for layer in enhancement_layers:
        if layer.base_id != base_id:
            raise ValueError(
                f"snapshot layer {layer.layer_id} base_id must match current base {base_id}"
            )
        _validate_layer_target(layer, unit_ids, anchor_segment_to_unit)
        if layer.layer_type == "vocabulary":
            _validate_vocabulary_layer_output(
                build_result,
                layer,
                unit_ids,
                units_by_id,
                anchor_segment_to_unit,
                anchor_segments_by_id,
            )
        elif layer.layer_type == "grammar_note":
            _validate_grammar_note_layer_output(
                build_result,
                layer,
                unit_ids,
                units_by_id,
                anchor_segment_to_unit,
                anchor_segments_by_id,
            )
        elif layer.layer_type == "sentence_analysis":
            _validate_sentence_analysis_layer_output(
                build_result,
                layer,
                unit_ids,
                units_by_id,
                anchor_segment_to_unit,
                anchor_segments_by_id,
            )

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
    if layer.layer_type == "translation" and layer.target_scope not in {"unit", "anchor_segment"}:
        raise ValueError(
            "translation snapshot layer "
            f"{layer.layer_id} must target a unit or anchor segment in D4"
        )
    if layer.layer_type == "vocabulary" and layer.target_scope != "unit":
        raise ValueError(
            "vocabulary snapshot layer "
            f"{layer.layer_id} must target a unit in D5-V2"
        )
    if layer.layer_type == "grammar_note" and layer.target_scope != "unit":
        raise ValueError(
            "grammar_note snapshot layer "
            f"{layer.layer_id} must target a unit in D5-V5"
        )
    if layer.layer_type == "sentence_analysis" and layer.target_scope != "unit":
        raise ValueError(
            "sentence_analysis snapshot layer "
            f"{layer.layer_id} must target a unit in D5-V5"
        )

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


def _build_default_snapshot_record(
    build_result: ReadingBaseBuildResult,
    snapshot_taken_at: datetime,
) -> ReaderSnapshotRecord:
    return ReaderSnapshotRecord(
        title=build_result.base.title_snapshot or "Untitled Reading",
        created_at=snapshot_taken_at,
        source_type="text",
        source_metadata={},
        generation=1,
        product_state="readable_enhancing",
        readiness_state="article_ready",
    )


def _build_default_enhancement_progress(
    layers: Sequence[ReaderSnapshotLayer],
) -> ReaderEnhancementProgress:
    capability_order = ("translation", "vocabulary", "grammar")
    progress_layers = [
        ReaderEnhancementProgressLayer(
            capability=(
                "grammar"
                if layer.layer_type in {"grammar_note", "sentence_analysis"}
                else layer.layer_type
            ),
            layer_type=layer.layer_type,
            status="succeeded",
            layer_id=layer.layer_id,
            target_scope=layer.target_scope,
            target_key=layer.target_key,
            created_at=layer.published_at,
            updated_at=layer.published_at,
        )
        for layer in layers
        if layer.layer_type in {"translation", "vocabulary", "grammar_note", "sentence_analysis"}
    ]
    capabilities_with_progress = {layer.capability for layer in progress_layers}
    progress_layers.extend(
        ReaderEnhancementProgressLayer(
            capability=capability,
            status="not_started",
        )
        for capability in capability_order
        if capability not in capabilities_with_progress
    )
    return ReaderEnhancementProgress(
        overall_status=(
            "ready"
            if progress_layers
            and all(layer.status == "succeeded" for layer in progress_layers)
            else "readable_enhancing"
        ),
        layers=progress_layers,
    )


def _build_plate_value(
    build_result: ReadingBaseBuildResult,
    enhancement_layers: Sequence[ReaderSnapshotLayer],
) -> list[dict[str, object]]:
    segments_by_unit = _group_segments(build_result.anchor_segments)
    translation_layers_by_target = _group_translation_layers(enhancement_layers)
    vocabulary_layers_by_unit = _group_vocabulary_layers(enhancement_layers)
    grammar_note_layers_by_unit = _group_grammar_note_layers(enhancement_layers)
    sentence_analysis_layers_by_unit = _group_sentence_analysis_layers(
        enhancement_layers
    )
    value: list[dict[str, object]] = []

    for unit in build_result.units:
        unit_segments = segments_by_unit[unit.unit_id]
        unit_children: list[dict[str, object]] = [
            _build_source_block(
                unit,
                unit_segments,
                vocabulary_layers=vocabulary_layers_by_unit.get(unit.unit_id, []),
                grammar_note_layers=grammar_note_layers_by_unit.get(unit.unit_id, []),
            )
        ]
        unit_children.extend(
            _build_translation_nodes(
                unit,
                translation_layers_by_target,
                unit_segments,
            )
        )
        unit_children.extend(
            _build_sentence_analysis_nodes(
                unit,
                sentence_analysis_layers_by_unit.get(unit.unit_id, []),
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
    *,
    vocabulary_layers: Sequence[ReaderSnapshotLayer] = (),
    grammar_note_layers: Sequence[ReaderSnapshotLayer] = (),
) -> dict[str, object]:
    children: list[dict[str, object]] = []
    cursor = 0
    unit_utf16_length = result_length_utf16(unit.text)
    vocabulary_marks_by_anchor = _build_vocabulary_marks_by_anchor(
        unit,
        unit_segments,
        vocabulary_layers,
    )
    grammar_note_marks_by_anchor = _build_grammar_note_marks_by_anchor(
        unit,
        unit_segments,
        grammar_note_layers,
    )

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
                "children": _build_segment_text_leaves(
                    segment,
                    vocabulary_marks=vocabulary_marks_by_anchor.get(
                        segment.anchor_segment_id,
                        [],
                    ),
                    grammar_note_marks=grammar_note_marks_by_anchor.get(
                        segment.anchor_segment_id,
                        [],
                    ),
                ),
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
    reader_vocabulary_marks: list[dict[str, object]] | None = None,
    reader_grammar_note_marks: list[dict[str, object]] | None = None,
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
    if reader_vocabulary_marks:
        leaf["reader_vocabulary_marks"] = reader_vocabulary_marks
    if reader_grammar_note_marks:
        leaf["reader_grammar_note_marks"] = reader_grammar_note_marks
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


def _group_vocabulary_layers(
    layers: Sequence[ReaderSnapshotLayer],
) -> dict[str, list[ReaderSnapshotLayer]]:
    return _group_unit_layers(layers, layer_type="vocabulary")


def _group_grammar_note_layers(
    layers: Sequence[ReaderSnapshotLayer],
) -> dict[str, list[ReaderSnapshotLayer]]:
    return _group_unit_layers(layers, layer_type="grammar_note")


def _group_sentence_analysis_layers(
    layers: Sequence[ReaderSnapshotLayer],
) -> dict[str, list[ReaderSnapshotLayer]]:
    return _group_unit_layers(layers, layer_type="sentence_analysis")


def _group_unit_layers(
    layers: Sequence[ReaderSnapshotLayer],
    *,
    layer_type: str,
) -> dict[str, list[ReaderSnapshotLayer]]:
    grouped: dict[str, list[ReaderSnapshotLayer]] = {}
    for layer in layers:
        if layer.layer_type != layer_type or layer.target_scope != "unit":
            continue
        grouped.setdefault(layer.target_key, []).append(layer)
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
            unit_segments=unit_segments,
            layers=translation_layers_by_target.get(("unit", unit.unit_id), []),
        )
    )
    for segment in unit_segments:
        nodes.extend(
            _build_translation_nodes_for_layers(
                unit=unit,
                unit_segments=unit_segments,
                layers=translation_layers_by_target.get(
                    ("anchor_segment", segment.anchor_segment_id),
                    [],
                ),
            )
        )
    return nodes


def _validate_vocabulary_layer_output(
    build_result: ReadingBaseBuildResult,
    layer: ReaderSnapshotLayer,
    unit_ids: set[str],
    units_by_id: dict[str, BuiltReadingUnit],
    anchor_segment_to_unit: dict[str, str],
    anchor_segments_by_id: dict[str, BuiltAnchorSegment],
) -> None:
    output = VocabularyLayerOutput.model_validate(layer.output)
    layer_unit = units_by_id.get(layer.target_key)
    if layer_unit is None:
        raise ValueError(
            "vocabulary snapshot layer "
            f"{layer.layer_id} target unit {layer.target_key} does not exist"
        )

    for index, item in enumerate(output.items):
        anchor = item.anchor
        context = (
            f"vocabulary snapshot layer {layer.layer_id} item {index} ({item.item_type})"
        )
        _validate_projected_text_range_anchor(
            anchor=anchor,
            expected_base_id=build_result.base.base_id,
            expected_unit_id=layer.target_key,
            unit_ids=unit_ids,
            unit_text=layer_unit.text,
            anchor_segment_to_unit=anchor_segment_to_unit,
            anchor_segments_by_id=anchor_segments_by_id,
            context=context,
        )


def _validate_grammar_note_layer_output(
    build_result: ReadingBaseBuildResult,
    layer: ReaderSnapshotLayer,
    unit_ids: set[str],
    units_by_id: dict[str, BuiltReadingUnit],
    anchor_segment_to_unit: dict[str, str],
    anchor_segments_by_id: dict[str, BuiltAnchorSegment],
) -> None:
    output = GrammarNoteLayerOutput.model_validate(layer.output)
    layer_unit = units_by_id.get(layer.target_key)
    if layer_unit is None:
        raise ValueError(
            "grammar_note snapshot layer "
            f"{layer.layer_id} target unit {layer.target_key} does not exist"
        )

    for item_index, item in enumerate(output.items):
        for span_index, anchor in enumerate(item.spans):
            context = (
                "grammar_note snapshot layer "
                f"{layer.layer_id} item {item_index} span {span_index}"
            )
            _validate_projected_text_range_anchor(
                anchor=anchor,
                expected_base_id=build_result.base.base_id,
                expected_unit_id=layer.target_key,
                unit_ids=unit_ids,
                unit_text=layer_unit.text,
                anchor_segment_to_unit=anchor_segment_to_unit,
                anchor_segments_by_id=anchor_segments_by_id,
                context=context,
            )


def _validate_sentence_analysis_layer_output(
    build_result: ReadingBaseBuildResult,
    layer: ReaderSnapshotLayer,
    unit_ids: set[str],
    units_by_id: dict[str, BuiltReadingUnit],
    anchor_segment_to_unit: dict[str, str],
    anchor_segments_by_id: dict[str, BuiltAnchorSegment],
) -> None:
    output = SentenceAnalysisLayerOutput.model_validate(layer.output)
    layer_unit = units_by_id.get(layer.target_key)
    if layer_unit is None:
        raise ValueError(
            "sentence_analysis snapshot layer "
            f"{layer.layer_id} target unit {layer.target_key} does not exist"
        )

    for item_index, item in enumerate(output.items):
        context = f"sentence_analysis snapshot layer {layer.layer_id} item {item_index}"
        _validate_projected_text_range_anchor(
            anchor=item.anchor,
            expected_base_id=build_result.base.base_id,
            expected_unit_id=layer.target_key,
            unit_ids=unit_ids,
            unit_text=layer_unit.text,
            anchor_segment_to_unit=anchor_segment_to_unit,
            anchor_segments_by_id=anchor_segments_by_id,
            context=context,
        )
        for chunk in item.chunks:
            if item.anchor.selected_text.find(chunk.text) < 0:
                raise ValueError(
                    f"{context} chunk text {chunk.text!r} is not grounded in selected_text"
                )


def _validate_projected_text_range_anchor(
    *,
    anchor: ReaderTextRangeAnchor,
    expected_base_id: str,
    expected_unit_id: str,
    unit_ids: set[str],
    unit_text: str,
    anchor_segment_to_unit: dict[str, str],
    anchor_segments_by_id: dict[str, BuiltAnchorSegment],
    context: str,
) -> BuiltAnchorSegment:
    _validate_snapshot_anchor(
        anchor,
        expected_base_id,
        unit_ids,
        anchor_segment_to_unit,
        context=context,
    )
    if anchor.unit_id != expected_unit_id:
        raise ValueError(
            f"{context} anchor unit_id {anchor.unit_id} "
            f"does not match target unit {expected_unit_id}"
        )

    segment = anchor_segments_by_id.get(anchor.anchor_segment_id)
    if segment is None:
        raise ValueError(
            f"{context} anchor segment {anchor.anchor_segment_id} does not exist"
        )
    if anchor.segment_type != segment.segment_type:
        raise ValueError(
            f"{context} segment_type {anchor.segment_type} "
            f"does not match {segment.segment_type}"
        )
    if anchor.sentence_id is not None and anchor.sentence_id != segment.sentence_id:
        raise ValueError(
            f"{context} sentence_id {anchor.sentence_id} does not match {segment.sentence_id}"
        )
    if (
        anchor.start_offset < segment.unit_start_utf16
        or anchor.end_offset > segment.unit_end_utf16
    ):
        raise ValueError(
            f"{context} offsets fall outside anchor segment {segment.anchor_segment_id}"
        )

    selected_text = slice_by_utf16_offsets(
        unit_text,
        anchor.start_offset,
        anchor.end_offset,
    )
    if selected_text is None:
        raise ValueError(f"{context} offsets do not slice target unit {expected_unit_id}")
    if selected_text != anchor.selected_text:
        raise ValueError(
            f"{context} selected_text does not match target unit {expected_unit_id}"
        )
    return segment


def _build_vocabulary_marks_by_anchor(
    unit: BuiltReadingUnit,
    unit_segments: Sequence[BuiltAnchorSegment],
    layers: Sequence[ReaderSnapshotLayer],
) -> dict[str, list[dict[str, object]]]:
    marks_by_anchor = {segment.anchor_segment_id: [] for segment in unit_segments}
    if not layers:
        return marks_by_anchor

    segments_by_id = {segment.anchor_segment_id: segment for segment in unit_segments}
    for layer in layers:
        output = VocabularyLayerOutput.model_validate(layer.output)
        for index, item in enumerate(output.items):
            anchor = item.anchor
            segment = segments_by_id.get(anchor.anchor_segment_id)
            if segment is None:
                continue
            marks_by_anchor[anchor.anchor_segment_id].append(
                _project_vocabulary_mark(
                    layer=layer,
                    item=item,
                    item_index=index,
                    segment=segment,
                )
            )

    for marks in marks_by_anchor.values():
        marks.sort(key=_vocabulary_mark_sort_key)
    return marks_by_anchor


def _build_grammar_note_marks_by_anchor(
    unit: BuiltReadingUnit,
    unit_segments: Sequence[BuiltAnchorSegment],
    layers: Sequence[ReaderSnapshotLayer],
) -> dict[str, list[dict[str, object]]]:
    marks_by_anchor = {segment.anchor_segment_id: [] for segment in unit_segments}
    if not layers:
        return marks_by_anchor

    segments_by_id = {segment.anchor_segment_id: segment for segment in unit_segments}
    for layer in layers:
        output = GrammarNoteLayerOutput.model_validate(layer.output)
        for item_index, item in enumerate(output.items):
            terminal_span_index = get_grammar_item_terminal_span_index(item.spans)
            for span_index, anchor in enumerate(item.spans):
                segment = segments_by_id.get(anchor.anchor_segment_id)
                if segment is None:
                    continue
                marks_by_anchor[anchor.anchor_segment_id].append(
                    _project_grammar_note_mark(
                        layer=layer,
                        item=item,
                        item_index=item_index,
                        span_index=span_index,
                        span_count=len(item.spans),
                        show_note_chip=span_index == terminal_span_index,
                        segment=segment,
                    )
                )

    for marks in marks_by_anchor.values():
        marks.sort(key=_grammar_note_mark_sort_key)
    return marks_by_anchor


def _project_vocabulary_mark(
    *,
    layer: ReaderSnapshotLayer,
    item: object,
    item_index: int,
    segment: BuiltAnchorSegment,
) -> dict[str, object]:
    anchor = item.anchor  # type: ignore[attr-defined]
    mark: dict[str, object] = {
        "mark_id": (
            f"{layer.layer_id}:{item.item_type}:{item_index}"  # type: ignore[attr-defined]
        ),
        "layer_id": layer.layer_id,
        "item_type": item.item_type,  # type: ignore[attr-defined]
        "anchor_segment_id": anchor.anchor_segment_id,
        "start_offset": anchor.start_offset,
        "end_offset": anchor.end_offset,
        "selected_text": anchor.selected_text,
        "segment_start_utf16": anchor.start_offset - segment.unit_start_utf16,
        "segment_end_utf16": anchor.end_offset - segment.unit_start_utf16,
    }
    item_type = item.item_type  # type: ignore[attr-defined]
    if item_type == "vocab_highlight":
        mark["headword"] = item.headword  # type: ignore[attr-defined]
        mark["brief_explanation"] = item.brief_explanation  # type: ignore[attr-defined]
        mark["reason"] = item.reason  # type: ignore[attr-defined]
    elif item_type == "phrase_gloss":
        mark["phrase"] = item.phrase  # type: ignore[attr-defined]
        mark["phrase_type"] = item.phrase_type  # type: ignore[attr-defined]
        mark["gloss"] = item.gloss  # type: ignore[attr-defined]
        mark["example"] = item.example  # type: ignore[attr-defined]
    elif item_type == "context_gloss":
        mark["display"] = item.display  # type: ignore[attr-defined]
        mark["gloss"] = item.gloss  # type: ignore[attr-defined]
        mark["reason"] = item.reason  # type: ignore[attr-defined]
    return mark


def _project_grammar_note_mark(
    *,
    layer: ReaderSnapshotLayer,
    item: object,
    item_index: int,
    span_index: int,
    span_count: int,
    show_note_chip: bool,
    segment: BuiltAnchorSegment,
) -> dict[str, object]:
    anchor = item.spans[span_index]  # type: ignore[attr-defined]
    item_id = build_grammar_item_id(layer.layer_id, item_index)
    return {
        "mark_id": f"{item_id}:span:{span_index}",
        "item_id": item_id,
        "owner": "system_ai",
        "layer_id": layer.layer_id,
        "item_type": "grammar_note",
        "anchor_segment_id": anchor.anchor_segment_id,
        "start_offset": anchor.start_offset,
        "end_offset": anchor.end_offset,
        "selected_text": anchor.selected_text,
        "segment_start_utf16": anchor.start_offset - segment.unit_start_utf16,
        "segment_end_utf16": anchor.end_offset - segment.unit_start_utf16,
        "span_index": span_index,
        "span_count": span_count,
        "show_note_chip": show_note_chip,
        "grammar_point": item.grammar_point,  # type: ignore[attr-defined]
        "pattern": item.pattern,  # type: ignore[attr-defined]
        "note": item.note,  # type: ignore[attr-defined]
    }


def _build_segment_text_leaves(
    segment: BuiltAnchorSegment,
    *,
    vocabulary_marks: Sequence[dict[str, object]],
    grammar_note_marks: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    if not vocabulary_marks and not grammar_note_marks:
        return [
            _build_stable_leaf(
                text=segment.text,
                source_role="segment_text",
                base_start_utf16=segment.base_start_utf16,
                base_end_utf16=segment.base_end_utf16,
                anchor_segment_id=segment.anchor_segment_id,
                segment_start_utf16=0,
                segment_end_utf16=segment.unit_end_utf16 - segment.unit_start_utf16,
            )
        ]

    segment_length = segment.unit_end_utf16 - segment.unit_start_utf16
    boundaries = {0, segment_length}
    for mark in [*vocabulary_marks, *grammar_note_marks]:
        boundaries.add(int(mark["segment_start_utf16"]))
        boundaries.add(int(mark["segment_end_utf16"]))

    leaves: list[dict[str, object]] = []
    ordered_boundaries = sorted(boundaries)
    for index in range(len(ordered_boundaries) - 1):
        part_start = ordered_boundaries[index]
        part_end = ordered_boundaries[index + 1]
        if part_end <= part_start:
            continue
        part_text = slice_by_utf16_offsets(segment.text, part_start, part_end)
        if part_text is None:
            raise ValueError(
                "segment "
                f"{segment.anchor_segment_id} projection slice "
                f"{part_start}:{part_end} is invalid"
            )
        active_vocabulary_marks = [
            _project_leaf_mark(mark, part_start=part_start, part_end=part_end)
            for mark in vocabulary_marks
            if int(mark["segment_start_utf16"]) < part_end
            and int(mark["segment_end_utf16"]) > part_start
        ]
        active_grammar_note_marks = [
            _project_leaf_mark(mark, part_start=part_start, part_end=part_end)
            for mark in grammar_note_marks
            if int(mark["segment_start_utf16"]) < part_end
            and int(mark["segment_end_utf16"]) > part_start
        ]
        leaves.append(
            _build_stable_leaf(
                text=part_text,
                source_role="segment_text",
                base_start_utf16=segment.base_start_utf16 + part_start,
                base_end_utf16=segment.base_start_utf16 + part_end,
                anchor_segment_id=segment.anchor_segment_id,
                segment_start_utf16=part_start,
                segment_end_utf16=part_end,
                reader_vocabulary_marks=active_vocabulary_marks or None,
                reader_grammar_note_marks=active_grammar_note_marks or None,
            )
        )
    return leaves


def _project_leaf_mark(
    mark: dict[str, object],
    *,
    part_start: int,
    part_end: int,
) -> dict[str, object]:
    leaf_mark = dict(mark)
    leaf_mark["starts_here"] = int(mark["segment_start_utf16"]) == part_start
    leaf_mark["ends_here"] = int(mark["segment_end_utf16"]) == part_end
    return leaf_mark


def _vocabulary_mark_sort_key(mark: dict[str, object]) -> tuple[int, int, int]:
    item_type = str(mark["item_type"])
    priority = {
        "context_gloss": 0,
        "phrase_gloss": 1,
        "vocab_highlight": 2,
    }.get(item_type, 99)
    span_length = int(mark["segment_end_utf16"]) - int(mark["segment_start_utf16"])
    return (
        int(mark["segment_start_utf16"]),
        -span_length,
        priority,
    )


def _grammar_note_mark_sort_key(mark: dict[str, object]) -> tuple[int, int, int, str]:
    span_length = int(mark["segment_end_utf16"]) - int(mark["segment_start_utf16"])
    return (
        int(mark["segment_start_utf16"]),
        -span_length,
        int(mark["span_index"]),
        str(mark["item_id"]),
    )


def _build_translation_nodes_for_layers(
    *,
    unit: BuiltReadingUnit,
    unit_segments: Sequence[BuiltAnchorSegment],
    layers: Sequence[ReaderSnapshotLayer],
) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
    segments_by_id = {
        segment.anchor_segment_id: segment for segment in unit_segments
    }
    for layer in layers:
        try:
            output = TranslationLayerOutput.model_validate(layer.output)
        except ValidationError:
            continue

        covered_anchor_segment_ids: set[str] = set()
        last_group_end_order = 0
        for group in output.groups:
            if not group.group_id.strip() or not group.translated_text.strip():
                continue

            group_segments: list[BuiltAnchorSegment] = []
            for anchor_segment_id in group.anchor_segment_ids:
                segment = segments_by_id.get(anchor_segment_id)
                if segment is None:
                    group_segments = []
                    break
                group_segments.append(segment)
            if not group_segments:
                continue

            order_indexes = [segment.order_index for segment in group_segments]
            if order_indexes != sorted(order_indexes) or any(
                current != previous + 1
                for previous, current in zip(order_indexes, order_indexes[1:], strict=False)
            ):
                continue

            first_segment = group_segments[0]
            last_segment = group_segments[-1]
            first_order = first_segment.order_index
            last_order = last_segment.order_index
            if first_order <= last_group_end_order:
                continue
            if any(
                anchor_segment_id in covered_anchor_segment_ids
                for anchor_segment_id in group.anchor_segment_ids
            ):
                continue

            span_start = first_segment.unit_start_utf16
            span_end = last_segment.unit_end_utf16
            if span_start > span_end:
                continue

            span_text = slice_by_utf16_offsets(unit.text, span_start, span_end)
            if span_text is None or not span_text:
                continue
            if compute_text_range_hash(span_text) != group.source_text_hash:
                continue

            covered_anchor_segment_ids.update(group.anchor_segment_ids)
            last_group_end_order = last_order
            nodes.append(
                {
                    "type": "reader_translation_group",
                    "owner": "system_ai",
                    "layer_id": layer.layer_id,
                    "layer_version": layer.schema_version,
                    "base_id": unit.base_id,
                    "unit_id": unit.unit_id,
                    "target_scope": layer.target_scope,
                    "target_key": layer.target_key,
                    "group_id": group.group_id,
                    "covered_anchor_segment_ids": list(group.anchor_segment_ids),
                    "source_text_hash": group.source_text_hash,
                    "children": [{"text": group.translated_text}],
                }
            )
    return nodes


def _build_sentence_analysis_nodes(
    unit: BuiltReadingUnit,
    layers: Sequence[ReaderSnapshotLayer],
) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = []
    for layer in layers:
        output = SentenceAnalysisLayerOutput.model_validate(layer.output)
        for item_index, item in enumerate(output.items):
            nodes.append(
                {
                    "type": "reader_sentence_analysis",
                    "owner": "system_ai",
                    "analysis_id": f"{layer.layer_id}:sentence_analysis:{item_index}",
                    "layer_id": layer.layer_id,
                    "layer_version": layer.schema_version,
                    "base_id": unit.base_id,
                    "unit_id": unit.unit_id,
                    "target_scope": layer.target_scope,
                    "target_key": layer.target_key,
                    "anchor_segment_id": item.anchor.anchor_segment_id,
                    "selected_text": item.anchor.selected_text,
                    "label": item.label,
                    "analysis": item.analysis,
                    "chunks": [
                        {
                            "order": chunk.order,
                            "label": chunk.label,
                            "text": chunk.text,
                        }
                        for chunk in item.chunks
                    ],
                    "children": [{"text": item.analysis}],
                }
            )
    return nodes
