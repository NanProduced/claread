"""Window selector: Z+ analysis candidate hard gates.

设计来源：docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  - §7.1 Dedup Key 设计（两层 + 按 item_type 拆分）
  - §7.2 处理流程（8 个 hard gates，按 item_type 拆分查询）
  - §7.3 Hard Gates 数值

核心约束：
  - grammar_note / sentence_analysis 各自独立 typed counters（dedup / quota / density）
  - gate 7 (ANCHOR_RATIO) 跨 item_type 聚合
  - gate 2 (PATTERN_DENSE) 仅对 grammar_note 生效
  - 排序：quality_score desc → reading_blocker true first → sentence_analysis > grammar_note
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SelectionGate(str, Enum):
    """§7.2 8 个 hard gates（按顺序检查，第一个不通过即拒绝）。"""

    DUP = "DUP"
    PATTERN_DENSE = "PATTERN_DENSE"
    ANCHOR_CAP = "ANCHOR_CAP"
    WINDOW_CAP = "WINDOW_CAP"
    RECORD_DENSITY = "RECORD_DENSITY"
    RECORD_BUDGET = "RECORD_BUDGET"
    ANCHOR_RATIO = "ANCHOR_RATIO"
    MULTI_UNIT_SPAN = "MULTI_UNIT_SPAN"


# §7.3 Hard Gates 数值
PER_ANCHOR_CAP = 1
PATTERN_DENSE_THRESHOLD = 3
ANCHOR_RATIO_THRESHOLD = 0.30


@dataclass(frozen=True, slots=True)
class SelectorLedger:
    """Plan ledger 当前状态（typed counters，按 item_type 分桶）。

    所有 counter 按 ``item_type`` 拆分查询，避免 sentence_analysis 占掉
    grammar_note 的 per-anchor quota，或被 grammar_pattern 误 dedup。
    """

    budget_used: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "grammar_note": {"count": 0},
            "sentence_analysis": {"count": 0},
        }
    )
    budget_total: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "grammar_note": {"count": 18},
            "sentence_analysis": {"count": 5},
        }
    )
    published_anchor_counts_by_type: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "grammar_note": {},
            "sentence_analysis": {},
        }
    )
    published_dedup_keys_by_type: dict[str, list[str]] = field(
        default_factory=lambda: {
            "grammar_note": [],
            "sentence_analysis": [],
        }
    )
    published_pattern_keys_by_type: dict[str, list[str]] = field(
        default_factory=lambda: {
            "grammar_note": [],
            "sentence_analysis": [],
        }
    )
    density_by_record: dict[str, int] = field(
        default_factory=lambda: {
            "grammar_note": 0,
            "sentence_analysis": 0,
        }
    )
    density_cap: dict[str, int] = field(
        default_factory=lambda: {
            "grammar_note": 3,
            "sentence_analysis": 1,
        }
    )
    total_anchors: int = 0
    annotated_anchors: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class CandidateItem:
    """Selector 输入：LLM 产出的候选标注。"""

    item_type: str  # "grammar_note" | "sentence_analysis"
    anchor_segment_id: str
    spans: list[dict[str, Any]]
    semantic_dedup_key: str
    pattern_key: str | None
    quality_score: float = 0.0
    reading_blocker: bool = False


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    candidate: CandidateItem
    gate: SelectionGate
    reason: str


@dataclass(frozen=True, slots=True)
class SelectionResult:
    accepted: list[CandidateItem]
    rejected: list[RejectedCandidate]


def select_candidates(
    candidates: list[CandidateItem],
    *,
    ledger: SelectorLedger,
    window_budget: dict[str, int],
) -> SelectionResult:
    """§7.2 处理流程：8 个 hard gates 按 item_type 拆分查询。

    排序键（§7.2 step 5）：
      - quality_score desc
      - reading_blocker true first
      - sentence_analysis > grammar_note
    所有 counter 按 ``candidate.item_type`` 查询 ledger 的对应分桶。
    """
    accepted: list[CandidateItem] = []
    rejected: list[RejectedCandidate] = []
    window_count_by_type: dict[str, int] = {"grammar_note": 0, "sentence_analysis": 0}

    sorted_candidates = sorted(
        candidates,
        key=lambda c: (
            -c.quality_score,
            0 if c.reading_blocker else 1,
            # sentence_analysis (0) > grammar_note (1)
            0 if c.item_type == "sentence_analysis" else 1,
        ),
    )

    for candidate in sorted_candidates:
        gate_failure = _check_gates(candidate, ledger, window_count_by_type, window_budget)
        if gate_failure is not None:
            rejected.append(
                RejectedCandidate(
                    candidate=candidate,
                    gate=gate_failure[0],
                    reason=gate_failure[1],
                )
            )
            continue
        accepted.append(candidate)
        window_count_by_type[candidate.item_type] = (
            window_count_by_type.get(candidate.item_type, 0) + 1
        )

    return SelectionResult(accepted=accepted, rejected=rejected)


def _check_gates(
    candidate: CandidateItem,
    ledger: SelectorLedger,
    window_count_by_type: dict[str, int],
    window_budget: dict[str, int],
) -> tuple[SelectionGate, str] | None:
    """按顺序检查 8 个 gates，返回第一个失败的 (gate, reason)，全部通过返回 None。

    所有 counter 按 ``candidate.item_type`` 查询 ledger 的对应分桶，
    gate 7 (ANCHOR_RATIO) 跨 item_type 聚合。
    """
    item_type = candidate.item_type

    # gate 1 (DUP): semantic_dedup_key(item_type) 已在 ledger
    if candidate.semantic_dedup_key in ledger.published_dedup_keys_by_type.get(item_type, []):
        return (
            SelectionGate.DUP,
            f"semantic_dedup_key {candidate.semantic_dedup_key} already published for {item_type}",
        )

    # gate 2 (PATTERN_DENSE): pattern_key 出现 >= 3 次（仅 grammar_note）
    if item_type == "grammar_note" and candidate.pattern_key:
        pattern_count = (
            ledger.published_pattern_keys_by_type.get(item_type, []).count(candidate.pattern_key)
        )
        if pattern_count >= PATTERN_DENSE_THRESHOLD:
            return (
                SelectionGate.PATTERN_DENSE,
                f"pattern {candidate.pattern_key} too dense ({pattern_count} >= {PATTERN_DENSE_THRESHOLD})",
            )

    # gate 3 (ANCHOR_CAP): anchor 已达 cap (per_anchor_cap=1)
    anchor_count = (
        ledger.published_anchor_counts_by_type.get(item_type, {}).get(
            candidate.anchor_segment_id, 0
        )
    )
    if anchor_count >= PER_ANCHOR_CAP:
        return (
            SelectionGate.ANCHOR_CAP,
            f"anchor {candidate.anchor_segment_id} reached cap for {item_type}",
        )

    # gate 4 (WINDOW_CAP): 当前 window 已发布 count >= window_budget
    if window_count_by_type.get(item_type, 0) >= window_budget.get(item_type, 0):
        return (
            SelectionGate.WINDOW_CAP,
            f"window {item_type} budget exhausted ({window_count_by_type.get(item_type, 0)} >= {window_budget.get(item_type, 0)})",
        )

    # gate 5 (RECORD_DENSITY): record density >= density_cap
    density = ledger.density_by_record.get(item_type, 0)
    density_cap = ledger.density_cap.get(item_type, 0)
    if density >= density_cap:
        return (
            SelectionGate.RECORD_DENSITY,
            f"record {item_type} density {density} >= cap {density_cap}",
        )

    # gate 6 (RECORD_BUDGET): budget_used.count >= budget_total.count
    used = ledger.budget_used.get(item_type, {"count": 0}).get("count", 0)
    total = ledger.budget_total.get(item_type, {"count": 0}).get("count", 0)
    if used >= total:
        return (
            SelectionGate.RECORD_BUDGET,
            f"record {item_type} budget {used} >= total {total}",
        )

    # gate 7 (ANCHOR_RATIO): annotated_anchor_ratio > 0.30（跨 item_type 聚合）
    # 检查 ledger 当前状态；candidate 接受后由调用方更新 annotated_anchors。
    if ledger.total_anchors > 0:
        current_ratio = len(ledger.annotated_anchors) / ledger.total_anchors
        if current_ratio > ANCHOR_RATIO_THRESHOLD:
            return (
                SelectionGate.ANCHOR_RATIO,
                f"annotated ratio {current_ratio:.2f} > {ANCHOR_RATIO_THRESHOLD}",
            )

    # gate 8 (MULTI_UNIT_SPAN): candidate spans 跨多个 unit_id
    unit_ids: set[str] = set()
    for span in candidate.spans:
        uid = span.get("unit_id")
        if uid is not None:
            unit_ids.add(uid)
    if len(unit_ids) > 1:
        return (
            SelectionGate.MULTI_UNIT_SPAN,
            f"spans cross units: {unit_ids}",
        )

    return None
