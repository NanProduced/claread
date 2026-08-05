"""Window planner: grammar-window analysis window 切分算法。

设计来源：docs/initiatives/reader-agentic-orchestration/modules/enhancement-layers-and-parsed.md
  - §5.1 边界类型
  - §5.2 切分算法（unit 不可拆）
  - §5.3 target/context 分离

核心约束：
  - 单个 Reading Unit 是最小不可拆单位，整体加入一个 window
  - ``char_count`` 用 ``unit_char_count``（来自 ``reading_units.base_*_utf16``），
    不是 ``sum(anchor_char_count)``
  - ``safety_max``（默认 3000）：单 window 超过则强制 finalize
  - ``target_max``（默认 1500）：达到则 finalize
  - oversized unit（超 safety_max）：仍整体放入，但标记 ``oversized_units``
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import NamedTuple

from .analysis_anchor_view import AnalysisAnchorView


# §5.1 isolation boundary block types：独立成 window
ISOLATION_BLOCK_TYPES: frozenset[str] = frozenset({
    "blockquote",
    "caption",
    "table",
    "table_row",
    "table_cell",
    "footnote",
    "image",
    "image_ocr",
})


@dataclass(frozen=True, slots=True)
class WindowFormationConfig:
    """§5.2 切分配置。"""

    target_max: int = 1500
    safety_max: int = 3000
    context_anchor_count: int = 2


@dataclass(slots=True)
class PlannedWindow:
    """§5.3 切分结果：target anchors + context anchors。

    ``target_anchors`` 是本 window 要分析的内容；
    ``context_anchor_prev`` / ``context_anchor_next`` 来自相邻 window，
    仅为 prompt 提供衔接上下文，不参与本 window 的分析预算。
    """

    window_index: int
    target_anchor_ids: list[str] = field(default_factory=list)
    target_unit_ids: list[str] = field(default_factory=list)
    target_block_ids: list[str] = field(default_factory=list)
    context_anchor_prev: list[AnalysisAnchorView] = field(default_factory=list)
    context_anchor_next: list[AnalysisAnchorView] = field(default_factory=list)
    char_count: int = 0
    anchor_count: int = 0
    oversized_units: list[str] = field(default_factory=list)
    target_anchors: list[AnalysisAnchorView] = field(default_factory=list)

    def add_unit(self, unit_id: str, anchors: list[AnalysisAnchorView]) -> None:
        """将整个 unit 加入 window。

        ``char_count`` 用 ``unit_char_count``（来自 reading_units.base_*_utf16），
        不是 ``sum(anchor_char_count)``。
        """
        self.target_unit_ids.append(unit_id)
        for a in anchors:
            self.target_anchors.append(a)
            self.target_anchor_ids.append(a.anchor_segment_id)
            if a.block_id and a.block_id not in self.target_block_ids:
                self.target_block_ids.append(a.block_id)
        # §5.2 关键：char_count 用 unit_char_count，不是 anchor 求和
        unit_chars = anchors[0].unit_char_count
        self.char_count += unit_chars
        self.anchor_count += len(anchors)


class _UnitMetadata(NamedTuple):
    """单个 reading unit 的切分元数据（内部用）。"""

    unit_id: str
    anchors: list[AnalysisAnchorView]
    char_count: int
    dominant_block_type: str
    contains_isolation: bool
    contains_code_block_only: bool


def plan_windows(
    sorted_anchor_views: tuple[AnalysisAnchorView, ...],
    *,
    config: WindowFormationConfig,
) -> list[PlannedWindow]:
    """§5.2 切分算法：unit 不可拆，按 unit 分组加入 window。

    边界类型（§5.1）：
      - ``paragraph`` / ``list_item`` / ``unknown``: soft boundary（可累积到 window）
      - ``heading``: hard boundary + section context（累积到 ``pending_section_context``）
      - ``blockquote`` / ``caption`` / ``table`` / ``table_row`` / ``table_cell``
        / ``footnote`` / ``image`` / ``image_ocr``: isolation boundary（独立成 window）
      - ``code_block``: skip（grammar_bundle v1 不处理）
    """
    if not sorted_anchor_views:
        return []

    # 预处理：按 unit_id 分组（单个 unit 是最小不可拆单位）
    unit_groups: dict[str, list[AnalysisAnchorView]] = defaultdict(list)
    for view in sorted_anchor_views:
        unit_groups[view.unit_id].append(view)

    # 每个 unit_group 计算 metadata
    unit_metadata: list[_UnitMetadata] = []
    for unit_id, anchors in unit_groups.items():
        anchors.sort(key=lambda a: a.unit_order_index)
        block_types = [a.block_type for a in anchors]
        dominant_block_type = max(set(block_types), key=block_types.count)
        unit_metadata.append(
            _UnitMetadata(
                unit_id=unit_id,
                anchors=anchors,
                char_count=anchors[0].unit_char_count,
                dominant_block_type=dominant_block_type,
                contains_isolation=any(bt in ISOLATION_BLOCK_TYPES for bt in block_types),
                contains_code_block_only=all(bt == "code_block" for bt in block_types),
            )
        )

    # 按 unit 的第一个 anchor 的 order_index 排序，保证文档顺序
    unit_metadata.sort(key=lambda m: m.anchors[0].order_index)

    context_count = config.context_anchor_count

    # 算法主循环
    windows: list[PlannedWindow] = []
    current_window = PlannedWindow(window_index=0)
    prev_context_anchors: list[AnalysisAnchorView] = []
    pending_section_context: list[AnalysisAnchorView] = []

    def finalize(window: PlannedWindow, prev_context: list[AnalysisAnchorView]) -> None:
        window.context_anchor_prev = (
            list(prev_context[-context_count:]) if prev_context else []
        )
        windows.append(window)

    for meta in unit_metadata:
        # 整 unit 是 code_block：skip（grammar_bundle v1 不处理）
        if meta.contains_code_block_only:
            continue

        # 整 unit 含 isolation block：独立成 window
        if meta.contains_isolation:
            if current_window.target_anchors:
                finalize(current_window, prev_context_anchors + pending_section_context)
                prev_context_anchors = list(current_window.target_anchors[-context_count:])
                pending_section_context = []
                current_window = PlannedWindow(window_index=len(windows))
            isolated = PlannedWindow(window_index=len(windows))
            isolated.add_unit(meta.unit_id, meta.anchors)
            finalize(isolated, prev_context_anchors + pending_section_context)
            prev_context_anchors = list(meta.anchors[-context_count:])
            pending_section_context = []
            current_window = PlannedWindow(window_index=len(windows))
            continue

        # heading：hard boundary，unit 整体进入 pending_section_context
        # （作为下一个 window 的 section context）
        if meta.dominant_block_type == "heading":
            if current_window.target_anchors:
                finalize(current_window, prev_context_anchors + pending_section_context)
                prev_context_anchors = list(current_window.target_anchors[-context_count:])
                pending_section_context = []
                current_window = PlannedWindow(window_index=len(windows))
            pending_section_context.extend(meta.anchors)
            continue

        # normal unit（paragraph / list_item / unknown）：soft boundary
        # safety_max 检查：若加入后超 safety_max 且当前 window 非空，先 finalize
        if (
            current_window.char_count + meta.char_count > config.safety_max
            and current_window.target_anchors
        ):
            finalize(current_window, prev_context_anchors + pending_section_context)
            prev_context_anchors = list(current_window.target_anchors[-context_count:])
            pending_section_context = []
            current_window = PlannedWindow(window_index=len(windows))

        current_window.add_unit(meta.unit_id, meta.anchors)
        # oversized unit 仍整体放入，但标记
        if meta.char_count > config.safety_max:
            current_window.oversized_units.append(meta.unit_id)

        # target_max 检查：达到则 finalize
        if current_window.char_count >= config.target_max:
            finalize(current_window, prev_context_anchors + pending_section_context)
            prev_context_anchors = list(current_window.target_anchors[-context_count:])
            pending_section_context = []
            current_window = PlannedWindow(window_index=len(windows))

    if current_window.target_anchors:
        finalize(current_window, prev_context_anchors + pending_section_context)

    # 第二次遍历：填充 context_anchor_next（取下一个 window 的前 N 个 anchor）
    for i, window in enumerate(windows):
        if i + 1 < len(windows):
            window.context_anchor_next = list(windows[i + 1].target_anchors[:context_count])

    return windows
