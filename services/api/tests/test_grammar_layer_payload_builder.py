"""Grammar_note extended ``layer_published`` payload builder tests.

验证 :func:`build_grammar_layer_published_payload` 从 typed
:class:`GrammarNoteLayerOutput` 正确派生 ``insertions[]`` descriptors。
"""

from __future__ import annotations

import inspect
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
            title="Builder Test",
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
        pattern=None,
        note=f"note for {grammar_point}。",
    )


def _base_payload(*, layer_id: str, target_key: str) -> dict[str, object]:
    return {
        "record_id": "rec_test",
        "base_id": "base-1",
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


def test_multi_anchor_multi_item_correct_grouping_coverage_order() -> None:
    """多 anchor、多 item 的 typed output 正确派生 descriptors。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2
    seg_0, seg_1 = segments[0], segments[1]

    # item 0 → seg_0, item 1 → seg_1, item 2 → seg_0
    items = [
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="A"),
        _make_grammar_item(seg_1, base_id=result.base.base_id, grammar_point="B"),
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="C"),
    ]
    layer_id = "layer_grammar_builder_multi"
    typed_output = _serialize_items(items)

    # anchor_order 按 source 阅读顺序：seg_0 在 seg_1 之前。
    anchor_order = (seg_0.anchor_segment_id, seg_1.anchor_segment_id)

    payload = build_grammar_layer_published_payload(
        base_payload=_base_payload(layer_id=layer_id, target_key=unit.unit_id),
        layer_id=layer_id,
        layer_type="grammar_note",
        target_key=unit.unit_id,
        typed_output=typed_output,
        anchor_order=anchor_order,
    )

    assert payload["schema_version"] == 1
    assert payload["operation"] == "insert_after_anchor"
    insertions = payload["insertions"]
    assert isinstance(insertions, list)
    assert len(insertions) == 2

    # descriptor 顺序遵循 anchor_order：seg_0 在前，seg_1 在后。
    assert insertions[0]["anchor_segment_id"] == seg_0.anchor_segment_id
    assert insertions[1]["anchor_segment_id"] == seg_1.anchor_segment_id

    # seg_0 的 descriptor 包含 item 0 和 item 2（按 item_index 升序）。
    desc_0 = insertions[0]
    assert desc_0["item_ids"] == [
        build_grammar_item_id(layer_id, 0),
        build_grammar_item_id(layer_id, 2),
    ]
    assert desc_0["unit_id"] == unit.unit_id
    assert desc_0["kind"] == "grammar_note"
    assert desc_0["layer_id"] == layer_id

    # seg_1 的 descriptor 包含 item 1。
    desc_1 = insertions[1]
    assert desc_1["item_ids"] == [build_grammar_item_id(layer_id, 1)]

    # 覆盖性：所有 item 恰好覆盖一次。
    all_item_ids = [iid for desc in insertions for iid in desc["item_ids"]]
    assert len(all_item_ids) == 3
    assert len(set(all_item_ids)) == 3


def test_none_anchor_order_raises_exception() -> None:
    """anchor_order 为 None 时抛出异常，不退化为字典序。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    items = [_make_grammar_item(segments[0], base_id=result.base.base_id, grammar_point="A")]
    typed_output = _serialize_items(items)

    with pytest.raises(GrammarLayerPayloadError, match="anchor_order is None"):
        build_grammar_layer_published_payload(
            base_payload=_base_payload(layer_id="layer_x", target_key=unit.unit_id),
            layer_id="layer_x",
            layer_type="grammar_note",
            target_key=unit.unit_id,
            typed_output=typed_output,
            anchor_order=None,
        )


def test_same_anchor_items_merge_into_one_descriptor() -> None:
    """同一 anchor 的多个 item 合并到一个 descriptor，item_ids 不重复，全覆盖。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    seg_0 = segments[0]

    items = [
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point=f"P{i}")
        for i in range(3)
    ]
    layer_id = "layer_grammar_builder_merge"
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
    desc = insertions[0]
    assert desc["anchor_segment_id"] == seg_0.anchor_segment_id
    # item_ids 按 item_index 升序，且唯一。
    assert desc["item_ids"] == [
        build_grammar_item_id(layer_id, 0),
        build_grammar_item_id(layer_id, 1),
        build_grammar_item_id(layer_id, 2),
    ]
    assert len(set(desc["item_ids"])) == 3


def test_empty_grammar_output_returns_base_payload_noop() -> None:
    """空 grammar output 返回 base_payload 不变（no-op）。"""
    base = _base_payload(layer_id="layer_empty", target_key="unit_1")

    # Case 1: typed_output 为空 dict。
    payload_empty_dict = build_grammar_layer_published_payload(
        base_payload=base,
        layer_id="layer_empty",
        layer_type="grammar_note",
        target_key="unit_1",
        typed_output={},
        anchor_order=("seg_1",),
    )
    assert payload_empty_dict is base
    assert "operation" not in payload_empty_dict
    assert "insertions" not in payload_empty_dict

    # Case 2: typed_output 的 items 为空列表（无法通过 min_length=1 校验）。
    payload_empty_items = build_grammar_layer_published_payload(
        base_payload=base,
        layer_id="layer_empty",
        layer_type="grammar_note",
        target_key="unit_1",
        typed_output={"schema_version": 1, "items": []},
        anchor_order=("seg_1",),
    )
    assert payload_empty_items is base
    assert "operation" not in payload_empty_items

    # Case 3: typed_output 缺少 items 字段（ValidationError）。
    payload_no_items = build_grammar_layer_published_payload(
        base_payload=base,
        layer_id="layer_empty",
        layer_type="grammar_note",
        target_key="unit_1",
        typed_output={"schema_version": 1},
        anchor_order=("seg_1",),
    )
    assert payload_no_items is base
    assert "insertions" not in payload_no_items


def test_item_id_consistency_with_snapshot_projection() -> None:
    """builder 产出的 item_id 与 snapshot projection 产出的一致。"""
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
    layer_id = "layer_grammar_builder_consistency"
    typed_output = _serialize_items(items)
    anchor_order = (seg_0.anchor_segment_id, seg_1.anchor_segment_id)

    # 通过 builder 生成 payload。
    payload = build_grammar_layer_published_payload(
        base_payload=_base_payload(layer_id=layer_id, target_key=unit.unit_id),
        layer_id=layer_id,
        layer_type="grammar_note",
        target_key=unit.unit_id,
        typed_output=typed_output,
        anchor_order=anchor_order,
    )

    # 通过 snapshot projection 生成 marks。
    layer = ReaderSnapshotLayer(
        layer_id=layer_id,
        layer_type="grammar_note",
        base_id=result.base.base_id,
        target_scope="unit",
        target_key=unit.unit_id,
        schema_version=1,
        output=typed_output,
        published_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )
    marks_by_anchor = _build_grammar_note_marks_by_anchor(unit, segments, [layer])

    # 收集 snapshot projection 的所有 item_id。
    snapshot_item_ids: set[str] = set()
    for marks in marks_by_anchor.values():
        for mark in marks:
            snapshot_item_ids.add(mark["item_id"])

    # 收集 builder payload 的所有 item_id。
    builder_item_ids: set[str] = set()
    for desc in payload["insertions"]:
        for item_id in desc["item_ids"]:
            builder_item_ids.add(item_id)

    # 两者完全一致。
    assert snapshot_item_ids == builder_item_ids

    # 逐 anchor 验证：builder descriptor 的 item_ids 与 snapshot marks 的 item_ids 一致。
    for desc in payload["insertions"]:
        anchor_id = desc["anchor_segment_id"]
        marks = marks_by_anchor[anchor_id]
        snapshot_ids_for_anchor = {m["item_id"] for m in marks}
        builder_ids_for_anchor = set(desc["item_ids"])
        assert snapshot_ids_for_anchor == builder_ids_for_anchor


def test_builder_does_not_accept_caller_assembled_id_lists() -> None:
    """builder 输入是 typed output，不接受调用方拼装的 ID 列表。

    验证两点：
    1. builder 签名没有 ``item_ids`` 参数。
    2. builder 内部通过 :func:`build_grammar_item_id` 从 ``layer_id`` + ``item_index``
       派生 item_ids，而非从 typed_output 中读取任何 caller 注入的 ID。
    """
    # 1. 签名校验：builder 不接受 item_ids 参数。
    sig = inspect.signature(build_grammar_layer_published_payload)
    assert "item_ids" not in sig.parameters
    # 输入参数是 typed_output（serialized GrammarNoteLayerOutput），不是 ID 列表。
    assert "typed_output" in sig.parameters

    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    seg_0 = segments[0]

    item = _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="A")
    layer_id = "layer_grammar_builder_no_caller_ids"

    # 2. 即使调用方试图在 typed_output 的 item 中注入 ``item_id`` 字段，
    #    GrammarNoteItem 的 extra="forbid" 会拒绝该字段。
    #    items 非空但无法解析 → 视为合同违反，抛出 GrammarLayerPayloadError
    # 触发同事务回滚（ fix：非空 corrupt output 不再静默回退到 base_payload）。
    item_dict = item.model_dump(mode="json")
    item_dict["item_id"] = "caller_injected_fake_id"
    typed_output_with_injection = {
        "schema_version": 1,
        "items": [item_dict],
    }

    base = _base_payload(layer_id=layer_id, target_key=unit.unit_id)
    with pytest.raises(GrammarLayerPayloadError, match="non-empty grammar output failed validation"):
        build_grammar_layer_published_payload(
            base_payload=base,
            layer_id=layer_id,
            layer_type="grammar_note",
            target_key=unit.unit_id,
            typed_output=typed_output_with_injection,
            anchor_order=(seg_0.anchor_segment_id,),
        )
    # 注入的 ID 不会被采用：调用方无法绕过 builder 派生 item_ids 的合同。

    # 3. 正常输入时，builder 产出的 item_ids 完全由 layer_id + item_index 决定，
    #    与 typed_output 中任何字段无关。
    typed_output_clean = _serialize_items([item])
    payload_clean = build_grammar_layer_published_payload(
        base_payload=base,
        layer_id=layer_id,
        layer_type="grammar_note",
        target_key=unit.unit_id,
        typed_output=typed_output_clean,
        anchor_order=(seg_0.anchor_segment_id,),
    )
    desc = payload_clean["insertions"][0]
    assert desc["item_ids"] == [build_grammar_item_id(layer_id, 0)]
    assert "caller_injected_fake_id" not in desc["item_ids"]


def test_anchor_not_in_anchor_order_raises() -> None:
    """item 的 anchor_segment_id 不在 anchor_order 中时抛出异常。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2
    seg_0, seg_1 = segments[0], segments[1]

    item = _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="A")
    typed_output = _serialize_items([item])

    # anchor_order 只包含 seg_1，但 item 的 anchor 是 seg_0。
    with pytest.raises(GrammarLayerPayloadError, match="not found in anchor_order"):
        build_grammar_layer_published_payload(
            base_payload=_base_payload(layer_id="layer_x", target_key=unit.unit_id),
            layer_id="layer_x",
            layer_type="grammar_note",
            target_key=unit.unit_id,
            typed_output=typed_output,
            anchor_order=(seg_1.anchor_segment_id,),
        )


def test_non_grammar_layer_type_raises() -> None:
    """非 grammar_note 的 layer_type 抛出异常。"""
    with pytest.raises(GrammarLayerPayloadError, match="only operates on grammar_note"):
        build_grammar_layer_published_payload(
            base_payload=_base_payload(layer_id="layer_x", target_key="unit_1"),
            layer_id="layer_x",
            layer_type="sentence_analysis",
            target_key="unit_1",
            typed_output={"schema_version": 1, "items": []},
            anchor_order=("seg_1",),
        )


def test_anchor_order_determines_descriptor_order_not_dict_sort() -> None:
    """descriptor 顺序使用 anchor_order，不退化为 anchor ID 字典序。"""
    result = _build_result()
    unit = result.units[0]
    segments = [s for s in result.anchor_segments if s.unit_id == unit.unit_id]
    assert len(segments) >= 2
    seg_0, seg_1 = segments[0], segments[1]

    # 构造 anchor_order 使其与字典序相反（如果 seg_0.anchor_segment_id < seg_1.anchor_segment_id）。
    # 如果两者顺序天然一致，反转 anchor_order 验证 builder 遵循输入顺序。
    items = [
        _make_grammar_item(seg_0, base_id=result.base.base_id, grammar_point="A"),
        _make_grammar_item(seg_1, base_id=result.base.base_id, grammar_point="B"),
    ]
    typed_output = _serialize_items(items)
    layer_id = "layer_grammar_builder_order"

    # 反转 anchor_order：seg_1 在前，seg_0 在后。
    reversed_order = (seg_1.anchor_segment_id, seg_0.anchor_segment_id)
    payload = build_grammar_layer_published_payload(
        base_payload=_base_payload(layer_id=layer_id, target_key=unit.unit_id),
        layer_id=layer_id,
        layer_type="grammar_note",
        target_key=unit.unit_id,
        typed_output=typed_output,
        anchor_order=reversed_order,
    )

    insertions = payload["insertions"]
    # descriptor 顺序遵循 reversed_order，不是字典序。
    assert insertions[0]["anchor_segment_id"] == seg_1.anchor_segment_id
    assert insertions[1]["anchor_segment_id"] == seg_0.anchor_segment_id
