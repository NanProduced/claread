"""Terminal span/anchor selection consistency tests.

 fix 验证：grammar_note item 的 descriptor ``anchor_segment_id`` 必须取
**terminal span**（与 snapshot projection 的 ``show_note_chip=true`` span 同源），
而非 ``spans[0]``。多 span 跨 anchor 时两者可能不同。

 fix 验证：非空但无法解析的 grammar output 必须抛出
:class:`GrammarLayerPayloadError` 触发同事务回滚，不再静默回退到 base_payload。

"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
    GrammarLayerPayloadError,
    build_grammar_item_id,
    build_grammar_layer_published_payload,
    get_grammar_item_terminal_span_index,
)
from app.services.reader_orchestration.snapshot import (
    _build_grammar_note_marks_by_anchor,
)

# 3 句文本，确保至少 3 个 anchor segment，用于多 terminal anchor 场景。
_PLAIN_TEXT = (
    "Not only did the team revise the plan, but they also clarified the timeline. "
    "Everyone understood the tradeoff. "
    "The schedule finally stabilized."
)


def _build_result():
    return build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id="record-terminal-anchor",
            base_id="base-terminal-anchor",
            source_text=_PLAIN_TEXT,
            title="Terminal Anchor Test",
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


def _make_multi_span_item(
    segments,
    *,
    base_id: str,
    grammar_point: str,
    note: str,
) -> GrammarNoteItem:
    """构造跨多 segment 的多 span item，每个 segment 取首词作为 selected_text。"""
    spans = [_make_anchor(seg, seg.text.split()[0], base_id) for seg in segments]
    return GrammarNoteItem(
        spans=spans,
        grammar_point=grammar_point,
        pattern=None,
        note=note,
    )


def _base_payload(*, layer_id: str, target_key: str) -> dict[str, object]:
    return {
        "record_id": "rec_terminal_anchor",
        "base_id": "base-terminal-anchor",
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


def _make_snapshot_layer(
    result,
    *,
    layer_id: str,
    typed_output: dict[str, object],
) -> ReaderSnapshotLayer:
    return ReaderSnapshotLayer(
        layer_id=layer_id,
        layer_type="grammar_note",
        base_id=result.base.base_id,
        target_scope="unit",
        target_key=result.units[0].unit_id,
        schema_version=1,
        output=typed_output,
        published_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )


def _snapshot_chip_anchor_for_item(
    marks_by_anchor: dict[str, list[dict[str, object]]],
    item_id: str,
) -> str:
    """从 snapshot projection 中找到指定 item 的 ``show_note_chip=true`` mark，
    返回其 ``anchor_segment_id``（即 terminal span 所在锚点）。
    """
    for marks in marks_by_anchor.values():
        for mark in marks:
            if mark["item_id"] == item_id and mark["show_note_chip"]:
                return mark["anchor_segment_id"]  # type: ignore[no-any-return]
    raise AssertionError(
        f"no show_note_chip=true mark found for item_id={item_id!r} in snapshot"
    )


# ---------------------------------------------------------------------------
# Terminal span/anchor selection
# ---------------------------------------------------------------------------


def test_spans_order_consistent_with_reading_order() -> None:
    """span[0] 在前（低 offset）、span[1] 在后（高 offset）→ terminal = span[1]。

    验证 builder 将 item 归到 span[1]（terminal）所在 anchor 下，
    且 snapshot projection 中该 item 的 ``show_note_chip=true`` span 的 anchor
    与 builder descriptor 的 anchor 完全一致。
    """
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2
    seg_0, seg_1 = segments[0], segments[1]

    # span[0] 在 seg_0（低 offset），span[1] 在 seg_1（高 offset）。
    # terminal = span[1]，位于 seg_1。
    item = _make_multi_span_item(
        [seg_0, seg_1],
        base_id=result.base.base_id,
        grammar_point="paired focus construction",
        note="跨段强调结构，callout 应附着在 terminal span。",
    )
    # 确认 terminal span 确实是 span[1]（位于 seg_1）。
    assert get_grammar_item_terminal_span_index(item.spans) == 1
    assert item.spans[1].anchor_segment_id == seg_1.anchor_segment_id

    layer_id = "layer_p0_consistent_order"
    typed_output = _serialize_items([item])
    anchor_order = (seg_0.anchor_segment_id, seg_1.anchor_segment_id)

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
    desc = insertions[0]
    # builder descriptor anchor = terminal span 的 anchor = seg_1。
    assert desc["anchor_segment_id"] == seg_1.anchor_segment_id
    expected_item_id = build_grammar_item_id(layer_id, 0)
    assert desc["item_ids"] == [expected_item_id]

    # snapshot projection 验证：chip-bearing span 的 anchor 与 descriptor 一致。
    snapshot_layer = _make_snapshot_layer(
        result, layer_id=layer_id, typed_output=typed_output
    )
    marks_by_anchor = _build_grammar_note_marks_by_anchor(unit, segments, [snapshot_layer])

    chip_anchor = _snapshot_chip_anchor_for_item(marks_by_anchor, expected_item_id)
    assert chip_anchor == desc["anchor_segment_id"], (
        f"payload descriptor anchor {desc['anchor_segment_id']!r} != "
        f"snapshot chip anchor {chip_anchor!r}"
    )


def test_spans_order_inconsistent_terminal_is_first_listed() -> None:
    """span[0] 在后（高 offset）、span[1] 在前（低 offset）→ terminal = span[0]。

    span 列表顺序与阅读顺序相反。terminal 仍由 ``(start_offset, end_offset,
    span_index)`` 的 max 决定（span[0]，因为其 offset 更高）。
    验证 builder 归到 span[0]（terminal）所在 anchor，snapshot chip anchor 一致。
    """
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2
    seg_0, seg_1 = segments[0], segments[1]

    # span[0] 在 seg_1（高 offset），span[1] 在 seg_0（低 offset）。
    # 列表顺序与阅读顺序相反；terminal = span[0]（max by tuple）。
    item = _make_multi_span_item(
        [seg_1, seg_0],
        base_id=result.base.base_id,
        grammar_point="reversed span order",
        note="span 列表顺序与阅读顺序相反，terminal 仍按 offset max 选取。",
    )
    assert get_grammar_item_terminal_span_index(item.spans) == 0
    assert item.spans[0].anchor_segment_id == seg_1.anchor_segment_id

    layer_id = "layer_p0_inconsistent_order"
    typed_output = _serialize_items([item])
    anchor_order = (seg_0.anchor_segment_id, seg_1.anchor_segment_id)

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
    desc = insertions[0]
    # builder descriptor anchor = terminal span 的 anchor = seg_1。
    assert desc["anchor_segment_id"] == seg_1.anchor_segment_id
    expected_item_id = build_grammar_item_id(layer_id, 0)
    assert desc["item_ids"] == [expected_item_id]

    # snapshot projection 验证。
    snapshot_layer = _make_snapshot_layer(
        result, layer_id=layer_id, typed_output=typed_output
    )
    marks_by_anchor = _build_grammar_note_marks_by_anchor(unit, segments, [snapshot_layer])

    chip_anchor = _snapshot_chip_anchor_for_item(marks_by_anchor, expected_item_id)
    assert chip_anchor == desc["anchor_segment_id"], (
        f"payload descriptor anchor {desc['anchor_segment_id']!r} != "
        f"snapshot chip anchor {chip_anchor!r}"
    )


def test_multi_item_across_multiple_terminal_anchors() -> None:
    """3 个 item，各自 terminal anchor 不同 → payload 3 个 descriptor，anchor 各异。

    - item 0：2 spans (seg_0, seg_1)，terminal = seg_1
    - item 1：2 spans (seg_1, seg_2)，terminal = seg_2
    - item 2：单 span (seg_0)，terminal = seg_0
    三个 terminal anchor 分别为 seg_1 / seg_2 / seg_0，互不相同。
    验证 payload 有 3 个 descriptor，且每个 descriptor 的 anchor 与 snapshot 中
    对应 item 的 chip-bearing span anchor 完全一致。
    """
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 3, "测试需要至少 3 个 anchor segment"
    seg_0, seg_1, seg_2 = segments[0], segments[1], segments[2]

    item_0 = _make_multi_span_item(
        [seg_0, seg_1],
        base_id=result.base.base_id,
        grammar_point="point A",
        note="terminal 在 seg_1。",
    )
    item_1 = _make_multi_span_item(
        [seg_1, seg_2],
        base_id=result.base.base_id,
        grammar_point="point B",
        note="terminal 在 seg_2。",
    )
    # 单 span item：terminal = span[0]，位于 seg_0。
    item_2 = GrammarNoteItem(
        spans=[_make_anchor(seg_0, seg_0.text.split()[0], result.base.base_id)],
        grammar_point="point C",
        pattern=None,
        note="单 span，terminal 在 seg_0。",
    )

    # 确认各 item 的 terminal anchor 互不相同。
    item_0_anchor_index = get_grammar_item_terminal_span_index(item_0.spans)
    assert item_0.spans[item_0_anchor_index].anchor_segment_id == seg_1.anchor_segment_id
    item_1_anchor_index = get_grammar_item_terminal_span_index(item_1.spans)
    assert item_1.spans[item_1_anchor_index].anchor_segment_id == seg_2.anchor_segment_id
    item_2_anchor_index = get_grammar_item_terminal_span_index(item_2.spans)
    assert item_2.spans[item_2_anchor_index].anchor_segment_id == seg_0.anchor_segment_id

    items = [item_0, item_1, item_2]
    layer_id = "layer_p0_multi_terminal"
    typed_output = _serialize_items(items)
    anchor_order = (seg_0.anchor_segment_id, seg_1.anchor_segment_id, seg_2.anchor_segment_id)

    payload = build_grammar_layer_published_payload(
        base_payload=_base_payload(layer_id=layer_id, target_key=unit.unit_id),
        layer_id=layer_id,
        layer_type="grammar_note",
        target_key=unit.unit_id,
        typed_output=typed_output,
        anchor_order=anchor_order,
    )

    insertions = payload["insertions"]
    # 3 个 terminal anchor → 3 个 descriptor。
    assert len(insertions) == 3
    # descriptor 按 anchor_order 排列：seg_0 / seg_1 / seg_2。
    assert insertions[0]["anchor_segment_id"] == seg_0.anchor_segment_id
    assert insertions[1]["anchor_segment_id"] == seg_1.anchor_segment_id
    assert insertions[2]["anchor_segment_id"] == seg_2.anchor_segment_id

    # 各 descriptor 的 item_index 归属：
    # seg_0 → item 2 (index 2)；seg_1 → item 0 (index 0)；seg_2 → item 1 (index 1)。
    assert insertions[0]["item_ids"] == [build_grammar_item_id(layer_id, 2)]
    assert insertions[1]["item_ids"] == [build_grammar_item_id(layer_id, 0)]
    assert insertions[2]["item_ids"] == [build_grammar_item_id(layer_id, 1)]

    # snapshot projection 验证：每个 descriptor 的 anchor 与对应 item 的
    # chip-bearing span anchor 一致。
    snapshot_layer = _make_snapshot_layer(
        result, layer_id=layer_id, typed_output=typed_output
    )
    marks_by_anchor = _build_grammar_note_marks_by_anchor(unit, segments, [snapshot_layer])

    for desc in insertions:
        desc_anchor = desc["anchor_segment_id"]
        for item_id in desc["item_ids"]:
            chip_anchor = _snapshot_chip_anchor_for_item(marks_by_anchor, item_id)
            assert chip_anchor == desc_anchor, (
                f"item_id={item_id!r}: payload descriptor anchor "
                f"{desc_anchor!r} != snapshot chip anchor {chip_anchor!r}"
            )


# ---------------------------------------------------------------------------
# Non-empty corrupt output must raise (no silent fallback)
# ---------------------------------------------------------------------------


def test_non_empty_corrupt_output_raises_not_silent_fallback() -> None:
    """非空但无法解析的 grammar output 必须抛出 GrammarLayerPayloadError。

    构造 items 非空但 span 缺少必填字段的 corrupt output（ValidationError），
    验证 builder 抛出 GrammarLayerPayloadError 而非静默返回 base_payload。
    这确保 corrupt output 无法绕过扩展合同与 validator。
    """
    base = _base_payload(layer_id="layer_p1_corrupt", target_key="unit_1")
    corrupt_typed_output = {
        "schema_version": 1,
        "items": [
            {
                "item_type": "grammar_note",
                # span 缺少 base_id / unit_id / start_offset / end_offset / selected_text
                # / text_hash / sentence_id / segment_type 等必填字段 → ValidationError。
                "spans": [{"anchor_segment_id": "seg_1"}],
                "grammar_point": "x",
                "note": "y",
            }
        ],
    }

    with pytest.raises(
        GrammarLayerPayloadError, match="non-empty grammar output failed validation"
    ):
        build_grammar_layer_published_payload(
            base_payload=base,
            layer_id="layer_p1_corrupt",
            layer_type="grammar_note",
            target_key="unit_1",
            typed_output=corrupt_typed_output,
            anchor_order=("seg_1",),
        )
