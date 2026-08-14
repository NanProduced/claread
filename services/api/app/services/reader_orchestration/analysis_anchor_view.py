"""AnalysisAnchorView: window planner 输入的派生视图。

设计来源：docs/architecture/reader-orchestration.md

通过 join 三张现有表构造：
  - ``anchor_segments`` (anchor_segment_id TEXT, base_*_utf16, unit_id)
  - ``reading_units`` (base_start_utf16, base_end_utf16) - 提供 unit_char_count
  - ``stable_document_blocks`` (block_id, block_type, canonical_text_*_utf16) - 提供 block_id (range intersection)

关键约束：
  - ``anchor_segment_id`` 是 TEXT，不是 UUID（与 grammar_worker.py GrammarCandidateSpan 一致）
  - ``unit_char_count`` 来自 ``reading_units.base_*_utf16``，不能用 ``sum(anchor_char_count)``
  - ``block_id`` 通过 range intersection 计算（不是 FK）
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg


@dataclass(frozen=True, slots=True)
class AnalysisAnchorView:
    anchor_segment_id: str
    """TEXT，与 grammar_worker GrammarCandidateSpan.anchor_segment_id 一致。"""

    anchor_row_id: UUID
    """anchor_segments.id（debug 用，不进 prompt）。"""

    unit_id: str
    unit_order_index: int
    base_id: UUID
    order_index: int
    base_start_utf16: int
    base_end_utf16: int
    unit_base_start_utf16: int
    unit_base_end_utf16: int
    unit_char_count: int
    """unit 长度（用于切分算法），= unit_base_end_utf16 - unit_base_start_utf16。"""

    block_id: str | None
    block_type: str
    canonical_text_start_utf16: int | None
    canonical_text_end_utf16: int | None
    anchor_char_count: int
    """anchor 自身长度（仅用于诊断，不用于切分）。"""

    crosses_block_boundary: bool = False


async def load_analysis_anchor_views(
    pool: asyncpg.Pool,
    *,
    base_id: UUID,
) -> tuple[AnalysisAnchorView, ...]:
    """加载 base 下所有 anchor + unit range + block range intersection 派生视图。

    数据源：
      - anchor_segments（anchor_segment_id TEXT, base_*_utf16, unit_id）
      - reading_units（base_start_utf16, base_end_utf16）
      - stable_document_blocks（block_id, block_type, canonical_text_*_utf16）

    返回按 ``order_index`` 升序排列的 ``AnalysisAnchorView`` 列表。
    """
    async with pool.acquire() as conn:
        anchor_rows = await conn.fetch(
            """
            SELECT
              a.id AS anchor_row_id,
              a.anchor_segment_id,
              a.unit_id,
              a.unit_order_index,
              a.base_id,
              a.order_index,
              a.base_start_utf16,
              a.base_end_utf16,
              u.base_start_utf16 AS unit_base_start_utf16,
              u.base_end_utf16 AS unit_base_end_utf16
            FROM anchor_segments a
            JOIN reading_units u
              ON u.base_id = a.base_id AND u.unit_id = a.unit_id
            WHERE a.base_id = $1
            ORDER BY a.order_index ASC
            """,
            base_id,
        )

        stable_document_id_row = await conn.fetchrow(
            """
            SELECT s.id AS stable_document_id
            FROM reading_bases b
            JOIN stable_reading_documents s
              ON s.reading_record_id = b.reading_record_id
              AND s.record_generation = b.record_generation
              AND s.status = 'active'
            WHERE b.id = $1
            """,
            base_id,
        )

        block_rows: list[asyncpg.Record] = []
        if stable_document_id_row is not None:
            stable_document_id = stable_document_id_row["stable_document_id"]
            block_rows = await conn.fetch(
                """
                SELECT
                  block_id, block_type, order_index,
                  canonical_text_start_utf16, canonical_text_end_utf16
                FROM stable_document_blocks
                WHERE stable_document_id = $1
                ORDER BY order_index ASC
                """,
                stable_document_id,
            )

        views: list[AnalysisAnchorView] = []
        for row in anchor_rows:
            block_id, block_type, canonical_start, canonical_end, crosses = _intersect_block(
                row["base_start_utf16"], row["base_end_utf16"], block_rows
            )
            unit_base_start = row["unit_base_start_utf16"]
            unit_base_end = row["unit_base_end_utf16"]
            base_start = row["base_start_utf16"]
            base_end = row["base_end_utf16"]
            views.append(
                AnalysisAnchorView(
                    anchor_segment_id=row["anchor_segment_id"],
                    anchor_row_id=row["anchor_row_id"],
                    unit_id=row["unit_id"],
                    unit_order_index=row["unit_order_index"],
                    base_id=row["base_id"],
                    order_index=row["order_index"],
                    base_start_utf16=base_start,
                    base_end_utf16=base_end,
                    unit_base_start_utf16=unit_base_start,
                    unit_base_end_utf16=unit_base_end,
                    unit_char_count=unit_base_end - unit_base_start,
                    block_id=block_id,
                    block_type=block_type,
                    canonical_text_start_utf16=canonical_start,
                    canonical_text_end_utf16=canonical_end,
                    anchor_char_count=base_end - base_start,
                    crosses_block_boundary=crosses,
                )
            )
        return tuple(views)


def _intersect_block(
    anchor_start: int,
    anchor_end: int,
    block_rows: list[asyncpg.Record],
) -> tuple[str | None, str, int | None, int | None, bool]:
    """对 anchor 的 ``[base_start, base_end)`` 与 blocks 的 ``canonical_text_*`` 区间求交。

    Returns:
        ``(block_id, block_type, canonical_start, canonical_end, crosses_boundary)``
    """
    candidates: list[tuple[asyncpg.Record, bool]] = []
    for b in block_rows:
        bs = b["canonical_text_start_utf16"]
        be = b["canonical_text_end_utf16"]
        if bs is None or be is None:
            continue  # image / image_ocr 无 text，不参与映射
        # 严格包含
        if bs <= anchor_start and be >= anchor_end:
            candidates.append((b, False))
        # 跨边界（部分重叠）
        elif bs < anchor_end and be > anchor_start:
            candidates.append((b, True))

    if not candidates:
        return None, "unknown", None, None, False

    # 多候选时取 order_index 最小的
    candidates.sort(key=lambda x: x[0]["order_index"])
    chosen, crosses = candidates[0]
    return (
        chosen["block_id"],
        chosen["block_type"],
        chosen["canonical_text_start_utf16"],
        chosen["canonical_text_end_utf16"],
        crosses,
    )
