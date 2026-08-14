"""Grammar layer payload builder and shared item identity helper.

实现 grammar_note 首发层 ``layer_published`` 事件 payload 合同
（）。

提供：
- :func:`build_grammar_item_id`：共享纯函数，生成 grammar item 的稳定 identity。
  snapshot projection 与 grammar payload builder MUST 调用同一 helper，
  不允许在两个模块复制字符串公式。
- :func:`build_grammar_layer_published_payload`：从 typed
  :class:`GrammarNoteLayerOutput` 自动派生 ``insertions[]`` descriptors，
  构造 grammar_note 首发的扩展 ``layer_published`` payload。

设计依据：
  docs/initiatives/reader-agentic-orchestration/modules/representation-event-contract.md
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from app.schemas.reader_orchestration import GrammarNoteLayerOutput, ReaderTextRangeAnchor


class GrammarLayerPayloadError(Exception):
    """grammar layer payload 构建过程中的合同违反。"""


def build_grammar_item_id(layer_id: str, item_index: int) -> str:
    """Build stable grammar item identity shared between snapshot projection and payload builder.

    公式：``f"{layer_id}:grammar_note:{item_index}"``。

    ``item_index`` 是 item 在 layer output ``items[]`` 列表中的全局位置索引（0-based）。
    snapshot projection 与 grammar payload builder MUST 调用同一 helper，
    不允许在两个模块复制字符串公式。
    """
    return f"{layer_id}:grammar_note:{item_index}"


def get_grammar_item_terminal_span_index(
    spans: Sequence[ReaderTextRangeAnchor],
) -> int:
    """Return the terminal span index for a grammar_note item.

    与 snapshot projection 的 callout-group 锚点选择保持完全一致：
    按 ``(start_offset, end_offset, span_index)`` 取 ``max``，仅该 terminal span
    在 snapshot 中被标记 ``show_note_chip=true``。

    payload builder MUST 调用本 helper 决定 descriptor 的
    ``anchor_segment_id``，确保 builder 声明的插入锚点与前端 callout-group
    实际附着的锚点（即 terminal span 所在锚点）一致。

    不允许在两个模块复制排序键公式。
    """
    return max(
        range(len(spans)),
        key=lambda span_index: (
            spans[span_index].start_offset,
            spans[span_index].end_offset,
            span_index,
        ),
    )


def build_grammar_layer_published_payload(
    *,
    base_payload: dict[str, Any],
    layer_id: str,
    layer_type: str,
    target_key: str,
    typed_output: dict[str, Any],
    anchor_order: tuple[str, ...] | None,
) -> dict[str, Any]:
    """Construct the extended ``layer_published`` payload for grammar_note.

    仅当 ``layer_type == "grammar_note"`` 时操作。从 typed
    :class:`GrammarNoteLayerOutput` 自动派生 ``insertions[]`` descriptors，
    不接受调用方拼装的任意 ID 列表。

    仅当 ``typed_output`` 确认为空（缺失/``None``/空 ``items``）时，返回
    ``base_payload`` 不变（保持既有 no-op 行为，不伪造空 insertions 的 insert
    payload）。非空 ``items`` 无法解析为 :class:`GrammarNoteLayerOutput` 时，
    抛出 :class:`GrammarLayerPayloadError`，使发布事务回滚。

    如果 ``anchor_order`` 为 ``None``，抛出
    :class:`GrammarLayerPayloadError`（阻断条件——不退化为 anchor ID 字典序）。

    返回 ``payload = base_payload + {"schema_version": 1,
    "operation": "insert_after_anchor", "insertions": [...]}``。
    """
    if layer_type != "grammar_note":
        raise GrammarLayerPayloadError(
            f"build_grammar_layer_published_payload only operates on grammar_note, "
            f"got layer_type={layer_type!r}"
        )

    # 空 grammar output：保持现有 no-op 行为（confirmed empty no-op）。
    # 不伪造空 insertions 的 insert payload。
    if not typed_output:
        return base_payload

    items = typed_output.get("items")
    if not items:
        # items 缺失、None 或空列表 → 确认的空 no-op，保持现有 7/10 字段 payload。
        return base_payload

    # 非空 items 必须能成功解析为 GrammarNoteLayerOutput；任何 ValidationError
    # 都视为合同违反并抛出 GrammarLayerPayloadError，触发同事务回滚，
    # 不再静默回退到 base_payload（避免 corrupt output 绕过扩展合同与 validator）。
    try:
        output = GrammarNoteLayerOutput.model_validate(typed_output)
    except ValidationError as exc:
        raise GrammarLayerPayloadError(
            f"non-empty grammar output failed validation: {exc}"
        ) from exc

    if anchor_order is None:
        # 阻断条件：anchor order 不可用时停止，不退化为 anchor ID 字典序。
        raise GrammarLayerPayloadError(
            "anchor_order is None; cannot determine descriptor order"
        )

    # 每个 item 的主 anchor 取 terminal span 的 anchor_segment_id，
    # 与 snapshot projection 的 callout-group 锚点（即 show_note_chip=true 的 span
    # 所在锚点）保持一致。多 span 跨 anchor 时不能使用第一个 span 的 anchor，
    # 否则 builder 声明的插入锚点会与前端 callout 实际锚点错位。
    items_by_anchor: dict[str, list[int]] = {}
    for item_index, item in enumerate(output.items):
        terminal_span_index = get_grammar_item_terminal_span_index(item.spans)
        primary_anchor = item.spans[terminal_span_index].anchor_segment_id
        if primary_anchor not in anchor_order:
            raise GrammarLayerPayloadError(
                f"item_index={item_index} anchor_segment_id={primary_anchor!r} "
                f"not found in anchor_order"
            )
        items_by_anchor.setdefault(primary_anchor, []).append(item_index)

    # descriptor 顺序使用 anchor_order（source/unit 的实际 anchor 阅读顺序）。
    insertions: list[dict[str, Any]] = []
    seen_anchors: set[str] = set()
    seen_item_ids: set[str] = set()
    total_covered_items = 0

    for anchor_segment_id in anchor_order:
        if anchor_segment_id not in items_by_anchor:
            continue
        if anchor_segment_id in seen_anchors:
            # grouping 已保证不重复，此处为防御性校验。
            raise GrammarLayerPayloadError(
                f"duplicate anchor_segment_id in insertions: {anchor_segment_id!r}"
            )
        seen_anchors.add(anchor_segment_id)

        # 同一 anchor 下的 item_ids 按原始 item_index 升序排列。
        item_indices = sorted(items_by_anchor[anchor_segment_id])
        item_ids: list[str] = []
        for item_index in item_indices:
            item_id = build_grammar_item_id(layer_id, item_index)
            if item_id in seen_item_ids:
                raise GrammarLayerPayloadError(
                    f"duplicate item_id across descriptors: {item_id!r}"
                )
            seen_item_ids.add(item_id)
            item_ids.append(item_id)

        insertions.append(
            {
                "unit_id": target_key,
                "anchor_segment_id": anchor_segment_id,
                "kind": "grammar_note",
                "layer_id": layer_id,
                "item_ids": item_ids,
            }
        )
        total_covered_items += len(item_indices)

    # 校验：所有 output items 恰好覆盖一次。
    if total_covered_items != len(output.items):
        raise GrammarLayerPayloadError(
            f"item coverage mismatch: covered {total_covered_items}, "
            f"expected {len(output.items)}"
        )

    # output.items 非空且每个 item 的 anchor 都在 anchor_order 中，
    # insertions 不可能为空；此处为防御性校验。
    if not insertions:
        raise GrammarLayerPayloadError(
            "no descriptors generated from non-empty grammar output"
        )

    payload = dict(base_payload)
    payload["schema_version"] = 1
    payload["operation"] = "insert_after_anchor"
    payload["insertions"] = insertions
    return payload
