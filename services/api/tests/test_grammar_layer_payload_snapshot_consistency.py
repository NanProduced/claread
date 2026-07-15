"""T4.2a-PUX-R4-R2.2-P2b-R1: snapshot projection consistency test.

以真实 snapshot projection 验证 grammar payload 的每个 ``item_id`` 可被准确定位
到对应的 grammar callout（spec: "以真实 snapshot projection 验证 item_id 可定位"）。

builder 与 snapshot projection 共享 :func:`build_grammar_item_id` helper，
对同一 layer 的相同 item_index 产出完全相同的 item_id。本测试验证：
1. payload ``insertions[].item_ids`` 中的每个 item_id 都能在 snapshot
   projection 的 grammar marks 中定位。
2. snapshot marks 的 ``item_id`` 与 payload descriptor 的 ``item_id`` 按 anchor
   分组完全一致。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.contracts.annotation import (
    compute_text_range_hash,
    utf16_code_unit_length,
)
from app.schemas.reader_orchestration import (
    GrammarNoteItem,
    ReaderSnapshotLayer,
    ReaderTextRangeAnchor,
)
from app.services.reader_orchestration import (
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
)
from app.services.reader_orchestration.grammar_layer_payload import (
    build_grammar_item_id,
    build_grammar_layer_published_payload,
)
from app.services.reader_orchestration.snapshot import (
    _build_grammar_note_marks_by_anchor,
)

_PLAIN_TEXT = (
    "Not only did the team revise the plan, but they also clarified the timeline. "
    "Everyone understood the tradeoff."
)


def _build_result():
    return build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id="record-snapshot",
            base_id="base-snapshot",
            source_text=_PLAIN_TEXT,
            title="Snapshot Consistency Test",
            language="en",
        )
    )


def _make_anchor(segment, selected_text: str, base_id: str) -> ReaderTextRangeAnchor:
    segment_text = segment.text
    selected_start = segment_text.index(selected_text)
    computed_start = segment.unit_start_utf16 + utf16_code_unit_length(
        segment_text[:selected_start]
    )
    computed_end = computed_start + utf16_code_unit_length(selected_text)
    return ReaderTextRangeAnchor(
        base_id=base_id,
        unit_id=segment.unit_id,
        anchor_segment_id=segment.anchor_segment_id,
        sentence_id=segment.sentence_id,
        segment_type=segment.segment_type,  # type: ignore[arg-type]
        start_offset=computed_start,
        end_offset=computed_end,
        selected_text=selected_text,
        text_hash=compute_text_range_hash(selected_text),
    )


def _make_grammar_item(segment, *, base_id: str, grammar_point: str) -> GrammarNoteItem:
    selected_text = segment.text.split()[0]
    anchor = _make_anchor(segment, selected_text, base_id)
    return GrammarNoteItem(
        spans=[anchor],
        grammar_point=grammar_point,
        pattern=f"pattern for {grammar_point}",
        note=f"note for {grammar_point}。",
    )


def _base_payload(*, layer_id: str, target_key: str) -> dict[str, object]:
    return {
        "record_id": "rec_snapshot",
        "base_id": "base-snapshot",
        "layer_id": layer_id,
        "layer_type": "grammar_note",
        "target_scope": "unit",
        "target_key": target_key,
        "generation": 1,
    }


def _serialize_items(items: list[GrammarNoteItem]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "items": [item.model_dump(mode="json") for item in items],
    }


def test_every_payload_item_id_locatable_in_snapshot_projection() -> None:
    """payload 的每个 item_id 都能在 snapshot projection 中准确定位。

    构造多 anchor、多 item 的 typed GrammarNoteLayerOutput：
    - item 0 → seg_0, item 1 → seg_1, item 2 → seg_0
    通过 builder 构造 payload，通过 snapshot projection 构造 marks，
    验证 payload ``insertions[].item_ids`` 的每个 item_id 都在 snapshot
    marks 中出现，且按 anchor 分组完全一致。
    """
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2
    seg_0, seg_1 = segments[0], segments[1]

    items = [
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="A"),
        _make_grammar_item(seg_1, base_id=result.base.base_id, grammar_point="B"),
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="C"),
    ]
    layer_id = "layer_grammar_snapshot_consistency"
    typed_output = _serialize_items(items)
    anchor_order = (seg_0.anchor_segment_id, seg_1.anchor_segment_id)

    # 1. 通过 builder 构造扩展 payload。
    payload = build_grammar_layer_published_payload(
        base_payload=_base_payload(layer_id=layer_id, target_key=unit.unit_id),
        layer_id=layer_id,
        layer_type="grammar_note",
        target_key=unit.unit_id,
        typed_output=typed_output,
        anchor_order=anchor_order,
    )
    assert payload["operation"] == "insert_after_anchor"
    insertions = payload["insertions"]
    assert len(insertions) == 2

    # 2. 通过真实 snapshot projection 构造 grammar marks。
    snapshot_layer = ReaderSnapshotLayer(
        layer_id=layer_id,
        layer_type="grammar_note",
        base_id=result.base.base_id,
        target_scope="unit",
        target_key=unit.unit_id,
        schema_version=1,
        output=typed_output,
        published_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )
    marks_by_anchor = _build_grammar_note_marks_by_anchor(
        unit, segments, [snapshot_layer]
    )

    # 3. 验证：payload 的每个 item_id 都能在 snapshot marks 中定位。
    #    收集 snapshot projection 的所有 item_id。
    snapshot_item_ids: set[str] = set()
    for marks in marks_by_anchor.values():
        for mark in marks:
            assert mark["item_id"]
            snapshot_item_ids.add(mark["item_id"])

    #    收集 payload 的所有 item_id。
    payload_item_ids: set[str] = set()
    for desc in insertions:
        for item_id in desc["item_ids"]:
            payload_item_ids.add(item_id)

    #    payload 的每个 item_id 都在 snapshot 中可定位。
    assert payload_item_ids.issubset(snapshot_item_ids), (
        f"payload item_ids not locatable in snapshot: "
        f"{payload_item_ids - snapshot_item_ids}"
    )

    # 4. 验证：按 anchor 分组，payload descriptor 的 item_ids 与 snapshot
    #    marks 的 item_ids 完全一致。
    for desc in insertions:
        anchor_id = desc["anchor_segment_id"]
        marks = marks_by_anchor[anchor_id]
        snapshot_ids_for_anchor = {m["item_id"] for m in marks}
        builder_ids_for_anchor = set(desc["item_ids"])
        assert snapshot_ids_for_anchor == builder_ids_for_anchor, (
            f"anchor {anchor_id!r}: snapshot item_ids "
            f"{snapshot_ids_for_anchor} != builder item_ids "
            f"{builder_ids_for_anchor}"
        )

    # 5. 覆盖性：payload 与 snapshot 覆盖的 item_id 集合完全一致。
    assert payload_item_ids == snapshot_item_ids, (
        f"payload item_ids {payload_item_ids} != snapshot item_ids "
        f"{snapshot_item_ids}"
    )


def test_single_anchor_multi_item_locatable_in_snapshot() -> None:
    """单 anchor 多 item 场景：所有 item_id 都能在 snapshot 中定位。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    seg_0 = segments[0]

    items = [
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point=f"P{i}")
        for i in range(3)
    ]
    layer_id = "layer_grammar_snapshot_single_anchor"
    typed_output = _serialize_items(items)
    anchor_order = (seg_0.anchor_segment_id,)

    payload = build_grammar_layer_published_payload(
        base_payload=_base_payload(layer_id=layer_id, target_key=unit.unit_id),
        layer_id=layer_id,
        layer_type="grammar_note",
        target_key=unit.unit_id,
        typed_output=typed_output,
        anchor_order=anchor_order,
    )
    insertions = payload["insertions"]
    assert len(insertions) == 1

    snapshot_layer = ReaderSnapshotLayer(
        layer_id=layer_id,
        layer_type="grammar_note",
        base_id=result.base.base_id,
        target_scope="unit",
        target_key=unit.unit_id,
        schema_version=1,
        output=typed_output,
        published_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )
    marks_by_anchor = _build_grammar_note_marks_by_anchor(
        unit, segments, [snapshot_layer]
    )

    desc = insertions[0]
    anchor_id = desc["anchor_segment_id"]
    marks = marks_by_anchor[anchor_id]
    snapshot_ids = {m["item_id"] for m in marks}
    builder_ids = set(desc["item_ids"])

    # 3 个 item 都能在 snapshot 中定位，且完全一致。
    assert builder_ids == snapshot_ids
    assert len(builder_ids) == 3
    # item_id 按 item_index 升序排列。
    expected_ids = {build_grammar_item_id(layer_id, i) for i in range(3)}
    assert builder_ids == expected_ids


def test_payload_item_ids_match_shared_helper_formula() -> None:
    """payload item_ids 与共享 helper 公式产出一致（snapshot projection 同源）。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2
    seg_0, seg_1 = segments[0], segments[1]

    items = [
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="A"),
        _make_grammar_item(seg_1, base_id=result.base.base_id, grammar_point="B"),
    ]
    layer_id = "layer_grammar_snapshot_helper_formula"
    typed_output = _serialize_items(items)
    anchor_order = (seg_0.anchor_segment_id, seg_1.anchor_segment_id)

    payload = build_grammar_layer_published_payload(
        base_payload=_base_payload(layer_id=layer_id, target_key=unit.unit_id),
        layer_id=layer_id,
        layer_type="grammar_note",
        target_key=unit.unit_id,
        typed_output=typed_output,
        anchor_order=anchor_order,
    )

    # item 0 → seg_0, item 1 → seg_1。
    # payload 的 item_id 与 helper 公式完全一致。
    all_item_ids = [
        item_id for desc in payload["insertions"] for item_id in desc["item_ids"]
    ]
    assert build_grammar_item_id(layer_id, 0) in all_item_ids
    assert build_grammar_item_id(layer_id, 1) in all_item_ids
    assert len(all_item_ids) == 2
