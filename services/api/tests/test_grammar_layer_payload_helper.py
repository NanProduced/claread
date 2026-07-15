"""T4.2a-PUX-R4-R2.2-P2b-R1: shared ``build_grammar_item_id`` helper tests.

验证 snapshot projection 与共享 helper 产出完全相同的 ``item_id`` 值。
snapshot projection 现在调用 :func:`build_grammar_item_id`，不再维护内联公式。
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
            reading_record_id="record-1",
            base_id="base-1",
            source_text=_PLAIN_TEXT,
            title="Helper Test",
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


def _make_grammar_note_layer(
    result,
    *,
    layer_id: str,
    items: list[GrammarNoteItem],
) -> ReaderSnapshotLayer:
    return ReaderSnapshotLayer(
        layer_id=layer_id,
        layer_type="grammar_note",
        base_id=result.base.base_id,
        target_scope="unit",
        target_key=result.units[0].unit_id,
        schema_version=1,
        output={
            "schema_version": 1,
            "items": [item.model_dump(mode="json") for item in items],
        },
        published_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )


def test_helper_formula_matches_expected_format() -> None:
    """helper 公式 ``f"{layer_id}:grammar_note:{item_index}"`` 保持向后兼容。"""
    assert build_grammar_item_id("layer_abc", 0) == "layer_abc:grammar_note:0"
    assert build_grammar_item_id("layer_abc", 42) == "layer_abc:grammar_note:42"


def test_snapshot_projection_uses_shared_helper_single_item() -> None:
    """snapshot projection 对单 item 产出的 item_id 与 helper 一致。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2

    selected_text = segments[0].text.split()[0]
    anchor = _make_anchor(segments[0], selected_text, result.base.base_id)
    item = GrammarNoteItem(
        spans=[anchor],
        grammar_point="paired focus construction",
        pattern="not only ... but also",
        note="前后两段共同强调并列信息。",
    )
    layer_id = "layer_grammar_helper_single"
    layer = _make_grammar_note_layer(result, layer_id=layer_id, items=[item])

    marks_by_anchor = _build_grammar_note_marks_by_anchor(unit, segments, [layer])
    marks = marks_by_anchor[segments[0].anchor_segment_id]
    assert len(marks) == 1

    projected_item_id = marks[0]["item_id"]
    assert projected_item_id == build_grammar_item_id(layer_id, 0)


def test_snapshot_projection_uses_shared_helper_multi_item_multi_anchor() -> None:
    """snapshot projection 对多 item、多 anchor 产出的 item_id 与 helper 一致。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2

    selected_text_0 = segments[0].text.split()[0]
    selected_text_1 = segments[1].text.split()[0]
    anchor_0 = _make_anchor(segments[0], selected_text_0, result.base.base_id)
    anchor_1 = _make_anchor(segments[1], selected_text_1, result.base.base_id)

    items = [
        GrammarNoteItem(
            spans=[anchor_0],
            grammar_point="point A",
            pattern="pattern A",
            note="note A。",
        ),
        GrammarNoteItem(
            spans=[anchor_1],
            grammar_point="point B",
            pattern="pattern B",
            note="note B。",
        ),
        GrammarNoteItem(
            spans=[anchor_0],
            grammar_point="point C",
            pattern=None,
            note="note C。",
        ),
    ]
    layer_id = "layer_grammar_helper_multi"
    layer = _make_grammar_note_layer(result, layer_id=layer_id, items=items)

    marks_by_anchor = _build_grammar_note_marks_by_anchor(unit, segments, [layer])

    # item 0 → anchor 0, item_index=0
    marks_anchor_0 = marks_by_anchor[segments[0].anchor_segment_id]
    item_ids_anchor_0 = sorted(m["item_id"] for m in marks_anchor_0 if m["item_id"])
    # item 1 → anchor 1, item_index=1
    marks_anchor_1 = marks_by_anchor[segments[1].anchor_segment_id]
    item_ids_anchor_1 = sorted(m["item_id"] for m in marks_anchor_1 if m["item_id"])

    # item 0 和 item 2 都属于 anchor 0（item_index 0 和 2）
    assert build_grammar_item_id(layer_id, 0) in item_ids_anchor_0
    assert build_grammar_item_id(layer_id, 2) in item_ids_anchor_0
    # item 1 属于 anchor 1（item_index 1）
    assert item_ids_anchor_1 == [build_grammar_item_id(layer_id, 1)]


def test_snapshot_projection_item_id_index_is_global_position() -> None:
    """item_index 是 items[] 中的全局位置（0-based），不是 anchor 内局部索引。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2

    # 3 个 item 都指向同一 anchor，验证 item_index 是 0, 1, 2 而非每次从 0 开始。
    selected_text = segments[0].text.split()[0]
    items = [
        GrammarNoteItem(
            spans=[_make_anchor(segments[0], selected_text, result.base.base_id)],
            grammar_point=f"point {i}",
            note=f"note {i}。",
        )
        for i in range(3)
    ]
    layer_id = "layer_grammar_helper_global_index"
    layer = _make_grammar_note_layer(result, layer_id=layer_id, items=items)

    marks_by_anchor = _build_grammar_note_marks_by_anchor(unit, segments, [layer])
    marks = marks_by_anchor[segments[0].anchor_segment_id]
    # 每个 item 有 1 个 span，所以 3 个 item → 3 个 mark
    assert len(marks) == 3
    projected_ids = {m["item_id"] for m in marks}
    expected_ids = {build_grammar_item_id(layer_id, i) for i in range(3)}
    assert projected_ids == expected_ids
