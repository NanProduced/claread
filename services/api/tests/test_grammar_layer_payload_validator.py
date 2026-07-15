"""T4.2a-PUX-R4-R2.2-P2b-R1: Grammar layer_published payload validator tests.

聚焦 grammar_note 首发的 layer_published 扩展 payload validator。
验证所有 spec 校验项（基础字段 / operation / insertions / descriptor /
脱敏 / 大小限制）。

参考 spec: ``.trae/specs/t42a-pux-r4-r2-2-p2b-r1-grammar-layer-payload/spec.md``
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.reader_orchestration.grammar_layer_payload_validator import (
    MAX_INSERTIONS,
    MAX_ITEM_IDS_PER_INSERTION,
    MAX_KEY_LENGTH,
    MAX_PAYLOAD_BYTES,
    validate_grammar_layer_published_payload,
)


def _make_valid_payload(**overrides: Any) -> dict[str, Any]:
    """构造一个合法的 grammar_note layer_published 扩展 payload 用于测试。

    默认包含 7 个基础字段 + 3 个扩展字段 + 1 个 descriptor。
    通过 ``overrides`` 可覆盖任意字段。
    """
    payload: dict[str, Any] = {
        # 7 个基础字段
        "record_id": "rec_test_001",
        "base_id": "base_test_001",
        "layer_id": "layer_grammar_test_001",
        "layer_type": "grammar_note",
        "target_scope": "unit",
        "target_key": "unit_1",
        "generation": 1,
        # 3 个扩展字段
        "schema_version": 1,
        "operation": "insert_after_anchor",
        "insertions": [
            {
                "unit_id": "unit_1",
                "anchor_segment_id": "seg_1",
                "kind": "grammar_note",
                "layer_id": "layer_grammar_test_001",
                "item_ids": [
                    "layer_grammar_test_001:grammar_note:0",
                    "layer_grammar_test_001:grammar_note:1",
                ],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _make_descriptor(
    *,
    unit_id: str = "unit_1",
    anchor_segment_id: str = "seg_1",
    kind: str = "grammar_note",
    layer_id: str = "layer_grammar_test_001",
    item_ids: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """构造一个合法的 insertion descriptor 用于测试。"""
    desc: dict[str, Any] = {
        "unit_id": unit_id,
        "anchor_segment_id": anchor_segment_id,
        "kind": kind,
        "layer_id": layer_id,
        "item_ids": item_ids
        if item_ids is not None
        else ["layer_grammar_test_001:grammar_note:0"],
    }
    desc.update(extra)
    return desc


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------


class TestValidPayload:
    """验证合法 payload 通过校验。"""

    def test_valid_payload_with_all_fields_passes(self) -> None:
        """包含全部字段的合法 payload 应通过校验。"""
        payload = _make_valid_payload()
        validate_grammar_layer_published_payload(payload)

    def test_valid_payload_with_window_metadata_passes(self) -> None:
        """包含 window publisher 兼容字段（source/plan_id/window_id）应通过。"""
        payload = _make_valid_payload(
            source="grammar_bundle_window",
            plan_id="plan_001",
            window_id="window_001",
        )
        validate_grammar_layer_published_payload(payload)

    def test_minimal_valid_payload_passes(self) -> None:
        """最小合法 payload（7 基础 + 3 扩展）应通过。"""
        payload = _make_valid_payload()
        validate_grammar_layer_published_payload(payload)

    def test_payload_with_multiple_insertions_passes(self) -> None:
        """多 anchor / 多 item 的 payload 应通过。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(
                anchor_segment_id="seg_1",
                item_ids=["layer_grammar_test_001:grammar_note:0"],
            ),
            _make_descriptor(
                anchor_segment_id="seg_2",
                item_ids=["layer_grammar_test_001:grammar_note:1"],
            ),
            _make_descriptor(
                anchor_segment_id="seg_3",
                item_ids=[
                    "layer_grammar_test_001:grammar_note:2",
                    "layer_grammar_test_001:grammar_note:3",
                ],
            ),
        ]
        validate_grammar_layer_published_payload(payload)


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


class TestRejectsDuplicateAnchor:
    def test_rejects_duplicate_anchor_segment_id(self) -> None:
        """insertions 中出现重复 anchor_segment_id 应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(
                anchor_segment_id="seg_1",
                item_ids=["layer_grammar_test_001:grammar_note:0"],
            ),
            _make_descriptor(
                anchor_segment_id="seg_1",  # 重复
                item_ids=["layer_grammar_test_001:grammar_note:1"],
            ),
        ]
        with pytest.raises(ValueError, match="duplicate anchor_segment_id"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsDuplicateItemId:
    def test_rejects_duplicate_item_id_across_descriptors(self) -> None:
        """跨 descriptor 的 item_ids 出现重复应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(
                anchor_segment_id="seg_1",
                item_ids=["dup_item_id"],
            ),
            _make_descriptor(
                anchor_segment_id="seg_2",
                item_ids=["dup_item_id"],  # 重复
            ),
        ]
        with pytest.raises(ValueError, match="duplicate item_id"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsCrossUnit:
    def test_rejects_cross_unit_descriptor(self) -> None:
        """descriptor unit_id != payload target_key 应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(unit_id="unit_2"),  # != target_key "unit_1"
        ]
        with pytest.raises(ValueError, match="unit_id"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsWrongLayerId:
    def test_rejects_wrong_layer_id_in_descriptor(self) -> None:
        """descriptor layer_id != payload layer_id 应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(layer_id="layer_other"),
        ]
        with pytest.raises(ValueError, match="layer_id"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsWrongKind:
    def test_rejects_wrong_kind_in_descriptor(self) -> None:
        """descriptor kind != payload layer_type 应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(kind="sentence_analysis"),
        ]
        with pytest.raises(ValueError, match="kind"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsUnknownOperation:
    def test_rejects_unknown_operation_replace(self) -> None:
        """operation='replace' 应拒绝（不在 allowlist）。"""
        payload = _make_valid_payload(operation="replace")
        with pytest.raises(ValueError, match="operation"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_unknown_operation_remove(self) -> None:
        """operation='remove' 应拒绝。"""
        payload = _make_valid_payload(operation="remove")
        with pytest.raises(ValueError, match="operation"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsEmptyInsertions:
    def test_rejects_empty_insertions_when_operation_is_insert_after_anchor(
        self,
    ) -> None:
        """operation=insert_after_anchor 但 insertions 为空应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = []
        with pytest.raises(ValueError, match="empty"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsTooManyInsertions:
    def test_rejects_too_many_insertions(self) -> None:
        """insertions 数量超过 64 应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(
                anchor_segment_id=f"seg_{i}",
                item_ids=[f"layer_grammar_test_001:grammar_note:{i}"],
            )
            for i in range(MAX_INSERTIONS + 1)
        ]
        with pytest.raises(ValueError, match="out of range"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsTooManyItemIds:
    def test_rejects_too_many_item_ids_per_descriptor(self) -> None:
        """单个 descriptor 的 item_ids 超过 64 应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(
                item_ids=[
                    f"layer_grammar_test_001:grammar_note:{i}"
                    for i in range(MAX_ITEM_IDS_PER_INSERTION + 1)
                ],
            ),
        ]
        with pytest.raises(ValueError, match="out of range"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsKeyTooLong:
    def test_rejects_anchor_segment_id_too_long(self) -> None:
        """anchor_segment_id 超过 256 chars 应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(anchor_segment_id="s" * (MAX_KEY_LENGTH + 1)),
        ]
        with pytest.raises(ValueError, match="exceeds limit"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_item_id_too_long(self) -> None:
        """item_id 超过 256 chars 应拒绝。"""
        payload = _make_valid_payload()
        payload["insertions"] = [
            _make_descriptor(item_ids=["i" * (MAX_KEY_LENGTH + 1)]),
        ]
        with pytest.raises(ValueError, match="exceeds limit"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsOversizedPayload:
    def test_rejects_payload_size_exceeds_16kb(self) -> None:
        """序列化 payload 超过 16KB 应拒绝。

        使用超长 base_id（无长度限制）使序列化结果 > MAX_PAYLOAD_BYTES。
        """
        payload = _make_valid_payload(base_id="b" * (MAX_PAYLOAD_BYTES + 100))
        with pytest.raises(ValueError, match="exceeds limit"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsForbiddenKey:
    def test_rejects_forbidden_key_note(self) -> None:
        """顶层出现 forbidden key 'note' 应拒绝。"""
        payload = _make_valid_payload()
        payload["note"] = "leaked content"
        with pytest.raises(ValueError, match="forbidden key"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_forbidden_key_selected_text(self) -> None:
        """顶层出现 forbidden key 'selected_text' 应拒绝。"""
        payload = _make_valid_payload()
        payload["selected_text"] = "leaked"
        with pytest.raises(ValueError, match="forbidden key"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_forbidden_key_raw_output(self) -> None:
        """顶层出现 forbidden key 'raw_output' 应拒绝。"""
        payload = _make_valid_payload()
        payload["raw_output"] = {"internal": "data"}
        with pytest.raises(ValueError, match="forbidden key"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_forbidden_key_in_descriptor(self) -> None:
        """descriptor 内出现 forbidden key 应拒绝（递归检查）。"""
        payload = _make_valid_payload()
        payload["insertions"][0]["note"] = "leaked"
        with pytest.raises(ValueError, match="forbidden key"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_forbidden_substring_in_key(self) -> None:
        """key 含 forbidden 子串（如 'annotation' 含 'note'）应拒绝。"""
        payload = _make_valid_payload()
        payload["annotation_text"] = "leaked"
        with pytest.raises(ValueError, match="forbidden key"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsUnknownTopLevelKey:
    def test_rejects_unknown_top_level_key(self) -> None:
        """出现不在白名单的顶层 key 应拒绝。"""
        payload = _make_valid_payload()
        payload["extra_field"] = "bad"
        with pytest.raises(ValueError, match="unexpected top-level keys"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsMissingBaseField:
    def test_rejects_missing_record_id(self) -> None:
        """缺少 record_id 应拒绝。"""
        payload = _make_valid_payload()
        del payload["record_id"]
        with pytest.raises(ValueError, match="record_id"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_missing_base_id(self) -> None:
        """缺少 base_id 应拒绝。"""
        payload = _make_valid_payload()
        del payload["base_id"]
        with pytest.raises(ValueError, match="base_id"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_missing_layer_id(self) -> None:
        """缺少 layer_id 应拒绝。"""
        payload = _make_valid_payload()
        del payload["layer_id"]
        with pytest.raises(ValueError, match="layer_id"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_missing_target_key(self) -> None:
        """缺少 target_key 应拒绝。"""
        payload = _make_valid_payload()
        del payload["target_key"]
        with pytest.raises(ValueError, match="target_key"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_missing_generation(self) -> None:
        """缺少 generation 应拒绝。"""
        payload = _make_valid_payload()
        del payload["generation"]
        with pytest.raises(ValueError, match="generation"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsWrongLayerType:
    def test_rejects_layer_type_not_grammar_note(self) -> None:
        """layer_type != 'grammar_note' 应拒绝。"""
        payload = _make_valid_payload(layer_type="sentence_analysis")
        with pytest.raises(ValueError, match="layer_type"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsInvalidGeneration:
    def test_rejects_generation_zero(self) -> None:
        """generation=0 应拒绝（必须 >0）。"""
        payload = _make_valid_payload(generation=0)
        with pytest.raises(ValueError, match="generation"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_generation_negative(self) -> None:
        """generation<0 应拒绝。"""
        payload = _make_valid_payload(generation=-1)
        with pytest.raises(ValueError, match="generation"):
            validate_grammar_layer_published_payload(payload)

    def test_rejects_generation_bool(self) -> None:
        """generation=True 应拒绝（bool 不是 int）。"""
        payload = _make_valid_payload(generation=True)
        with pytest.raises(ValueError, match="generation"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsWrongSchemaVersion:
    def test_rejects_schema_version_not_1(self) -> None:
        """schema_version != 1 应拒绝。"""
        payload = _make_valid_payload(schema_version=2)
        with pytest.raises(ValueError, match="schema_version"):
            validate_grammar_layer_published_payload(payload)


class TestRejectsWrongTargetScope:
    def test_rejects_target_scope_not_unit(self) -> None:
        """target_scope != 'unit' 应拒绝。"""
        payload = _make_valid_payload(target_scope="article")
        with pytest.raises(ValueError, match="target_scope"):
            validate_grammar_layer_published_payload(payload)
