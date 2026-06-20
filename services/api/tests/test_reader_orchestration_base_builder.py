from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

import pytest

from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.schemas.reader_orchestration import (
    ReaderSnapshotAskSupplement,
    ReaderSnapshotLayer,
    ReaderSnapshotParsedDecision,
    ReaderSnapshotUserAsset,
    ReaderUnitAnchor,
)
from app.services.reader_orchestration import (
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
    build_reader_plate_snapshot,
    result_length_utf16,
)


def _build_result(source_text: str):
    return build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id="record-1",
            base_id="base-1",
            source_text=source_text,
            title="Sample Title",
            language="en",
        )
    )


def _build_translation_layer(
    *,
    base_id: str,
    target_scope: str,
    target_key: str,
    layer_id: str = "layer-1",
) -> ReaderSnapshotLayer:
    return ReaderSnapshotLayer(
        layer_id=layer_id,
        layer_type="translation",
        base_id=base_id,
        target_scope=target_scope,  # type: ignore[arg-type]
        target_key=target_key,
        schema_version=1,
        output={
            "schema_version": 1,
            "target_language": "zh-CN",
            "translated_text": "示例译文",
            "notes": [],
            "confidence": "normal",
        },
        published_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
    )


def _assert_whitespace_only_gaps(
    text: str,
    spans: list[tuple[int, int]],
) -> None:
    assert spans
    previous_end = None
    for start, end in spans:
        assert start < end
        if previous_end is not None:
            assert start >= previous_end
            gap = slice_by_utf16_offsets(text, previous_end, start)
            assert gap is None or not gap.strip()
        previous_end = end


def _collect_stable_text(nodes: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for node in nodes:
        if "text" in node and isinstance(node["text"], str):
            parts.append(node["text"])
            continue
        children = node.get("children")
        if isinstance(children, list):
            parts.append(
                _collect_stable_text(
                    [child for child in children if isinstance(child, dict)]
                )
            )
    return "".join(parts)


@pytest.mark.parametrize(
    ("text", "expected_length", "start_offset", "end_offset", "expected_slice"),
    [
        ("A😀B", 4, 1, 3, "😀"),
        ("“Smart” — dash…", 15, 0, 7, "“Smart”"),
        ("中文🙂mix", 7, 0, 4, "中文🙂"),
        ("“Hi” — 中文🙂!", 12, 7, 11, "中文🙂"),
    ],
)
def test_utf16_length_and_slicing_cover_unicode_corpus(
    text: str,
    expected_length: int,
    start_offset: int,
    end_offset: int,
    expected_slice: str,
) -> None:
    assert utf16_code_unit_length(text) == expected_length
    assert slice_by_utf16_offsets(text, start_offset, end_offset) == expected_slice


@pytest.mark.parametrize(
    ("text", "expected_hash"),
    [
        ("ASCII sentence.", "4ddf2a29"),
        ("“Smart” — dash…", "4955e1b1"),
        ("emoji 😀 pair", "dc0f6f0e"),
        ("混合🙂text", "c042bede"),
        (" ", "250c8f7f"),
        ("", "811c9dc5"),
    ],
)
def test_fnv1a32_utf16_hash_parity_corpus(text: str, expected_hash: str) -> None:
    assert compute_text_range_hash(text) == expected_hash


def test_builder_covers_visible_text_without_overlap() -> None:
    source_text = (
        "  “First” sentence. Second sentence!\n"
        "Still same paragraph.\n\n"
        "Short heading\n\n"
        "Clause only block without punctuation"
    )
    result = _build_result(source_text)

    assert result.base.text
    assert result.units
    assert result.anchor_segments

    unit_spans = [(unit.base_start_utf16, unit.base_end_utf16) for unit in result.units]
    _assert_whitespace_only_gaps(result.base.text, unit_spans)

    first_gap = slice_by_utf16_offsets(result.base.text, 0, result.units[0].base_start_utf16)
    assert first_gap is None or not first_gap.strip()
    last_gap = slice_by_utf16_offsets(
        result.base.text,
        result.units[-1].base_end_utf16,
        result_length_utf16(result.base.text),
    )
    assert last_gap is None or not last_gap.strip()

    unit_by_id = {unit.unit_id: unit for unit in result.units}
    grouped_segments = defaultdict(list)
    for segment in result.anchor_segments:
        grouped_segments[segment.unit_id].append(segment)
        assert segment.text == slice_by_utf16_offsets(
            result.base.text,
            segment.base_start_utf16,
            segment.base_end_utf16,
        )
        unit = unit_by_id[segment.unit_id]
        assert segment.text == slice_by_utf16_offsets(
            unit.text,
            segment.unit_start_utf16,
            segment.unit_end_utf16,
        )

    for unit in result.units:
        assert unit.text == slice_by_utf16_offsets(
            result.base.text,
            unit.base_start_utf16,
            unit.base_end_utf16,
        )
        segments = sorted(grouped_segments[unit.unit_id], key=lambda item: item.unit_order_index)
        assert segments
        local_spans = [(segment.unit_start_utf16, segment.unit_end_utf16) for segment in segments]
        _assert_whitespace_only_gaps(unit.text, local_spans)

    assert [unit.order_index for unit in result.units] == sorted(
        unit.order_index for unit in result.units
    )
    assert [segment.order_index for segment in result.anchor_segments] == sorted(
        segment.order_index for segment in result.anchor_segments
    )


def test_builder_marks_fallback_window_segments_low_quality() -> None:
    source_text = (
        "this block has no sentence punctuation and enough words to force the deterministic "
        "fallback window segmentation because it keeps going with connector words and emoji 🙂 "
        "mixed in for offset coverage across the entire paragraph without a full stop "
        "anywhere at all"
    )
    result = _build_result(source_text)

    assert len(result.units) == 1
    assert result.units[0].boundary_quality == "low"
    assert result.units[0].unit_type == "fallback"
    assert all(segment.segment_type == "fallback_window" for segment in result.anchor_segments)
    assert all(segment.boundary_quality == "low" for segment in result.anchor_segments)


def test_reader_plate_snapshot_source_leaves_rebuild_stable_base_slices() -> None:
    result = _build_result("First sentence. Second sentence!\n\nAnother paragraph.")
    snapshot = build_reader_plate_snapshot(
        result,
        snapshot_taken_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        last_event_sequence=7,
    )

    unit_by_id = {unit.unit_id: unit for unit in result.units}
    assert len(snapshot.value) == len(result.units)
    for unit_node in snapshot.value:
        unit_id = unit_node["unit_id"]
        unit = unit_by_id[unit_id]  # type: ignore[index]
        source_block = unit_node["children"][0]  # type: ignore[index]
        rebuilt_text = _collect_stable_text(source_block["children"])  # type: ignore[index]
        assert rebuilt_text == unit.text
        assert rebuilt_text == slice_by_utf16_offsets(
            result.base.text,
            unit.base_start_utf16,
            unit.base_end_utf16,
        )


def test_reader_plate_snapshot_uses_schema_kind_and_last_event_sequence() -> None:
    result = _build_result("One sentence only.")
    snapshot = build_reader_plate_snapshot(
        result,
        snapshot_taken_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        last_event_sequence=11,
    )
    payload = snapshot.model_dump(mode="json")

    assert payload["schema_kind"] == "reader_plate_snapshot"
    assert payload["last_event_sequence"] == 11
    assert "projection_version" not in payload


def test_reader_plate_snapshot_rebuild_is_stable_for_same_domain_facts() -> None:
    result = _build_result("Sentence one. Sentence two!\n\nAnother block for stability.")
    snapshot_taken_at = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)

    snapshot_a = build_reader_plate_snapshot(
        result,
        snapshot_taken_at=snapshot_taken_at,
        last_event_sequence=13,
    )
    snapshot_b = build_reader_plate_snapshot(
        result,
        snapshot_taken_at=snapshot_taken_at,
        last_event_sequence=13,
    )

    assert snapshot_a.model_dump(mode="json") == snapshot_b.model_dump(mode="json")


def test_reader_plate_snapshot_projects_translation_layer_in_top_level_and_value() -> None:
    result = _build_result("First sentence.\n\nSecond paragraph.")
    layer = _build_translation_layer(
        base_id=result.base.base_id,
        target_scope="unit",
        target_key=result.units[0].unit_id,
    )

    snapshot = build_reader_plate_snapshot(
        result,
        snapshot_taken_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        last_event_sequence=17,
        enhancement_layers=[layer],
    )

    translation_nodes = [
        child
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_translation"
    ]

    assert [layer.layer_id for layer in snapshot.enhancement_layers] == ["layer-1"]
    assert [node["layer_id"] for node in translation_nodes] == ["layer-1"]
    assert translation_nodes[0]["unit_id"] == result.units[0].unit_id
    assert translation_nodes[0]["base_id"] == result.base.base_id


def test_reader_plate_snapshot_rejects_wrong_base_translation_layer() -> None:
    result = _build_result("First sentence.\n\nSecond paragraph.")
    layer = _build_translation_layer(
        base_id="base-other",
        target_scope="unit",
        target_key=result.units[0].unit_id,
    )

    with pytest.raises(ValueError, match="base_id must match current base"):
        build_reader_plate_snapshot(
            result,
            snapshot_taken_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
            last_event_sequence=17,
            enhancement_layers=[layer],
        )


@pytest.mark.parametrize("owner_kind", ["supplement", "asset"])
def test_reader_plate_snapshot_rejects_wrong_base_anchor_inputs(owner_kind: str) -> None:
    result = _build_result("First sentence only.")
    anchor = ReaderUnitAnchor(
        base_id="base-other",
        unit_id=result.units[0].unit_id,
        text_hash=result.units[0].text_hash,
    )

    kwargs: dict[str, object] = {}
    if owner_kind == "supplement":
        kwargs["ask_supplements"] = [
            ReaderSnapshotAskSupplement(
                supplement_id="supp-1",
                anchor=anchor,
                content={"kind": "note"},
                created_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
            )
        ]
    else:
        kwargs["user_assets"] = [
            ReaderSnapshotUserAsset(
                asset_id="asset-1",
                asset_type="reader_note",
                anchor=anchor,
                updated_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
            )
        ]

    with pytest.raises(ValueError, match="anchor base_id must match current base"):
        build_reader_plate_snapshot(
            result,
            snapshot_taken_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
            last_event_sequence=17,
            **kwargs,
        )


def test_reader_plate_snapshot_rejects_parsed_decision_for_unknown_unit() -> None:
    result = _build_result("First sentence only.")
    parsed_decision = ReaderSnapshotParsedDecision(
        unit_id="u999",
        policy_code="translation_core",
        parsed_state="parsed",
    )

    with pytest.raises(ValueError, match="parsed decision unit_id"):
        build_reader_plate_snapshot(
            result,
            snapshot_taken_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
            last_event_sequence=17,
            parsed_decisions=[parsed_decision],
        )
