import pytest
from uuid import uuid4, UUID
from app.services.reader_orchestration.analysis_anchor_view import AnalysisAnchorView
from app.services.reader_orchestration.window_planner import (
    WindowFormationConfig,
    PlannedWindow,
    plan_windows,
)


def make_anchor(
    *,
    anchor_segment_id: str,
    unit_id: str,
    unit_order_index: int = 0,
    order_index: int = 0,
    unit_char_count: int = 100,
    block_type: str = "paragraph",
    base_start_utf16: int = 0,
    base_end_utf16: int = 100,
    unit_base_start_utf16: int = 0,
    unit_base_end_utf16: int = 100,
) -> AnalysisAnchorView:
    return AnalysisAnchorView(
        anchor_segment_id=anchor_segment_id,
        anchor_row_id=uuid4(),
        unit_id=unit_id,
        unit_order_index=unit_order_index,
        base_id=uuid4(),
        order_index=order_index,
        base_start_utf16=base_start_utf16,
        base_end_utf16=base_end_utf16,
        unit_base_start_utf16=unit_base_start_utf16,
        unit_base_end_utf16=unit_base_end_utf16,
        unit_char_count=unit_char_count,
        block_id="b1",
        block_type=block_type,
        canonical_text_start_utf16=base_start_utf16,
        canonical_text_end_utf16=base_end_utf16,
        anchor_char_count=base_end_utf16 - base_start_utf16,
        crosses_block_boundary=False,
    )


def test_plan_windows_unit_indivisible():
    """多个短 unit 应产生多个 window，每个 unit 只属于一个 window"""
    views = tuple(
        make_anchor(
            anchor_segment_id=f"a{i}",
            unit_id=f"u{i}",
            unit_order_index=0,
            order_index=i,
            unit_char_count=400,  # 5 个 400-char unit → target_max=1500 时切 2 window
            base_start_utf16=i * 400,
            base_end_utf16=(i + 1) * 400,
            unit_base_start_utf16=i * 400,
            unit_base_end_utf16=(i + 1) * 400,
        )
        for i in range(5)
    )
    config = WindowFormationConfig(target_max=1500, safety_max=3000, context_anchor_count=2)
    windows = plan_windows(views, config=config)

    assert 1 <= len(windows) <= 3

    # 每个 unit 只属于一个 window
    unit_to_windows: dict[str, set[int]] = {}
    for i, window in enumerate(windows):
        for unit_id in window.target_unit_ids:
            unit_to_windows.setdefault(unit_id, set()).add(i)
    for unit_id, window_indices in unit_to_windows.items():
        assert len(window_indices) == 1, f"unit {unit_id} appears in windows {window_indices}"


def test_plan_windows_oversized_unit_stays_intact():
    """单 unit 超 safety_max 仍整体放入 window，标记 oversized"""
    views = tuple([
        make_anchor(
            anchor_segment_id="a1",
            unit_id="oversized_unit_id",
            unit_char_count=4000,
            base_start_utf16=0,
            base_end_utf16=4000,
            unit_base_start_utf16=0,
            unit_base_end_utf16=4000,
        ),
    ])
    config = WindowFormationConfig(target_max=1500, safety_max=3000, context_anchor_count=2)
    windows = plan_windows(views, config=config)

    assert len(windows) == 1
    oversized_window = windows[0]
    assert "oversized_unit_id" in oversized_window.target_unit_ids
    assert oversized_window.char_count == 4000
    assert "oversized_unit_id" in oversized_window.oversized_units


def test_plan_windows_heading_creates_hard_boundary():
    """heading 触发 hard boundary，进入下一 window 的 context_anchor_prev"""
    views = tuple([
        make_anchor(anchor_segment_id="a1", unit_id="u1", order_index=0, unit_char_count=400,
                    base_start_utf16=0, base_end_utf16=400, unit_base_start_utf16=0, unit_base_end_utf16=400),
        make_anchor(anchor_segment_id="a2", unit_id="u2", order_index=1, unit_char_count=400, block_type="heading",
                    base_start_utf16=400, base_end_utf16=800, unit_base_start_utf16=400, unit_base_end_utf16=800),
        make_anchor(anchor_segment_id="a3", unit_id="u3", order_index=2, unit_char_count=400,
                    base_start_utf16=800, base_end_utf16=1200, unit_base_start_utf16=800, unit_base_end_utf16=1200),
    ])
    config = WindowFormationConfig(target_max=1500, safety_max=3000, context_anchor_count=2)
    windows = plan_windows(views, config=config)

    # u1 一个 window，heading u2 进入 pending_section_context，u3 一个 window
    assert len(windows) >= 2
    # 第二个 window 的 context_anchor_prev 应含 heading anchor
    assert any(a.block_type == "heading" for a in windows[-1].context_anchor_prev)


def test_plan_windows_isolation_block_independent_window():
    """blockquote 独立成 window"""
    views = tuple([
        make_anchor(anchor_segment_id="a1", unit_id="u1", order_index=0, unit_char_count=400,
                    base_start_utf16=0, base_end_utf16=400, unit_base_start_utf16=0, unit_base_end_utf16=400),
        make_anchor(anchor_segment_id="a2", unit_id="u2", order_index=1, unit_char_count=200, block_type="blockquote",
                    base_start_utf16=400, base_end_utf16=600, unit_base_start_utf16=400, unit_base_end_utf16=600),
        make_anchor(anchor_segment_id="a3", unit_id="u3", order_index=2, unit_char_count=400,
                    base_start_utf16=600, base_end_utf16=1000, unit_base_start_utf16=600, unit_base_end_utf16=1000),
    ])
    config = WindowFormationConfig(target_max=1500, safety_max=3000, context_anchor_count=2)
    windows = plan_windows(views, config=config)

    # blockquote 独立成 window
    blockquote_windows = [w for w in windows if any(a.block_type == "blockquote" for a in w.target_anchors)]
    assert len(blockquote_windows) == 1
    blockquote_window = blockquote_windows[0]
    assert blockquote_window.target_unit_ids == ["u2"]


def test_plan_windows_code_block_skipped():
    """code_block 整 unit 时 skip，不进入任何 window"""
    views = tuple([
        make_anchor(anchor_segment_id="a1", unit_id="u1", order_index=0, unit_char_count=400,
                    base_start_utf16=0, base_end_utf16=400, unit_base_start_utf16=0, unit_base_end_utf16=400),
        make_anchor(anchor_segment_id="a2", unit_id="u2", order_index=1, unit_char_count=400, block_type="code_block",
                    base_start_utf16=400, base_end_utf16=800, unit_base_start_utf16=400, unit_base_end_utf16=800),
        make_anchor(anchor_segment_id="a3", unit_id="u3", order_index=2, unit_char_count=400,
                    base_start_utf16=800, base_end_utf16=1200, unit_base_start_utf16=800, unit_base_end_utf16=1200),
    ])
    config = WindowFormationConfig(target_max=1500, safety_max=3000, context_anchor_count=2)
    windows = plan_windows(views, config=config)

    # code_block unit 不在任何 window 中
    for w in windows:
        assert "u2" not in w.target_unit_ids


def test_plan_windows_empty_input_returns_empty():
    """空输入返回空 list"""
    windows = plan_windows((), config=WindowFormationConfig())
    assert windows == []


def test_plan_windows_context_anchor_next_filled():
    """非最后 window 应填充 context_anchor_next"""
    views = tuple(
        make_anchor(
            anchor_segment_id=f"a{i}",
            unit_id=f"u{i}",
            order_index=i,
            unit_char_count=400,
            base_start_utf16=i * 400,
            base_end_utf16=(i + 1) * 400,
            unit_base_start_utf16=i * 400,
            unit_base_end_utf16=(i + 1) * 400,
        )
        for i in range(5)
    )
    config = WindowFormationConfig(target_max=800, safety_max=3000, context_anchor_count=2)
    windows = plan_windows(views, config=config)

    if len(windows) >= 2:
        # 非最后 window 的 context_anchor_next 应非空
        for w in windows[:-1]:
            assert len(w.context_anchor_next) > 0
        # 最后 window 的 context_anchor_next 应为空
        assert len(windows[-1].context_anchor_next) == 0


def test_plan_windows_char_count_uses_unit_char_count():
    """window.char_count 用 unit_char_count，不是 anchor_char_count 求和"""
    # 一个 unit 含 2 个 anchor，每个 anchor 100 chars，但 unit 总长 300 chars
    views = tuple([
        make_anchor(
            anchor_segment_id="a1", unit_id="u1", unit_order_index=0, order_index=0,
            unit_char_count=300,  # unit 总长
            base_start_utf16=0, base_end_utf16=100,  # anchor 100 chars
            unit_base_start_utf16=0, unit_base_end_utf16=300,
        ),
        make_anchor(
            anchor_segment_id="a2", unit_id="u1", unit_order_index=1, order_index=1,
            unit_char_count=300,
            base_start_utf16=200, base_end_utf16=300,
            unit_base_start_utf16=0, unit_base_end_utf16=300,
        ),
    ])
    config = WindowFormationConfig(target_max=1500, safety_max=3000, context_anchor_count=2)
    windows = plan_windows(views, config=config)

    assert len(windows) == 1
    # char_count 应是 300（unit 长度），不是 200（两个 anchor 长度和）
    assert windows[0].char_count == 300
    assert windows[0].anchor_count == 2
