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
    """§7.2 8 个 hard gates + INVALID_ANCHOR pre-filter（按顺序检查，第一个不通过即拒绝）。

    INVALID_ANCHOR 是 §7.2 step 2 的 pre-filter（非 8 gates 之一），用于拒绝
    anchor_segment_id ∉ target_anchor_ids 的 candidate。放在 gate 管道之前，
    使 rejection 可追踪。
    """

    INVALID_ANCHOR = "INVALID_ANCHOR"
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
    # §7.3 density_cap：每 1000 UTF-16 chars 的最大 annotation 数（ratio 上限）。
    # 旧实现误把它当作 raw count，P2-6 修正为 per-1000-chars ratio。
    # Phase 5: sentence_analysis 从 1.0 提升到 2.0，与 grammar_note 的 3.0
    # 保持对称。旧值 1.0 过紧，导致 sentence_analysis 在 RECORD_DENSITY 阶段
    # 被静默拒绝，无法与 grammar_note 在选点质量上公平竞争。
    density_cap: dict[str, float] = field(
        default_factory=lambda: {
            "grammar_note": 3.0,
            "sentence_analysis": 2.0,
        }
    )
    # base text 总长度（UTF-16 code units），用于将 density_by_record 折算为
    # 每 1000 chars 的 ratio。默认 0 时 density_denom 退化为 1.0，等价于 raw count。
    base_text_length_utf16: int = 0
    total_anchors: int = 0
    annotated_anchors: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class CandidateItem:
    """Selector 输入：LLM 产出的候选标注。

    content_* 字段携带 layer contract 所需的实际内容（grammar_point /
    note / label / analysis / chunks），由 executor 填充，publisher 据此
    构建 GrammarNoteLayerOutput / SentenceAnalysisLayerOutput。selector
    本身不读这些字段（只读 dedup/anchor/pattern/quality 字段）。
    """

    item_type: str  # "grammar_note" | "sentence_analysis"
    anchor_segment_id: str
    spans: list[dict[str, Any]]
    semantic_dedup_key: str
    pattern_key: str | None
    quality_score: float = 0.0
    reading_blocker: bool = False
    # Layer content fields (P1-4 bridge: executor → publisher)
    grammar_point: str = ""
    pattern: str | None = None
    note: str = ""
    label: str = ""
    analysis: str = ""
    chunks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    candidate: CandidateItem
    gate: SelectionGate
    reason: str


@dataclass(frozen=True, slots=True)
class SelectionResult:
    accepted: list[CandidateItem]
    rejected: list[RejectedCandidate]


@dataclass(slots=True)
class WindowRoundState:
    """当前 window 内已接受 candidate 的累计贡献（P1-5）。

    旧实现只读 ``ledger`` 的 pre-existing 状态，未把同一 window 内已接受的
    candidate 累计到后续 gate 检查上，导致：
      - 同一 window 内接受重复 ``semantic_dedup_key``
      - 同一 anchor 接受多个同 item_type item
      - 超过 record budget / density

    本类在每个 candidate 被接受后追加其贡献，``_check_gates`` 在检查
    DUP / PATTERN_DENSE / ANCHOR_CAP / RECORD_DENSITY / RECORD_BUDGET 时
    把这些值叠加到 ``ledger`` 之上。
    """

    dedup_keys_by_type: dict[str, list[str]] = field(
        default_factory=lambda: {
            "grammar_note": [],
            "sentence_analysis": [],
        }
    )
    anchor_counts_by_type: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "grammar_note": {},
            "sentence_analysis": {},
        }
    )
    pattern_keys_by_type: dict[str, list[str]] = field(
        default_factory=lambda: {
            "grammar_note": [],
            "sentence_analysis": [],
        }
    )
    density_by_type: dict[str, int] = field(
        default_factory=lambda: {
            "grammar_note": 0,
            "sentence_analysis": 0,
        }
    )
    budget_used_by_type: dict[str, int] = field(
        default_factory=lambda: {
            "grammar_note": 0,
            "sentence_analysis": 0,
        }
    )
    # P1-4: 跨 item_type 的已接受 anchor 集合（gate 7 ANCHOR_RATIO 用）。
    # gate 7 是跨 item_type 聚合的，所以不能用 anchor_counts_by_type，
    # 需要独立的 set 追踪所有 item_type 接受过的 anchor。
    accepted_anchors: set[str] = field(default_factory=set)

    def add(self, candidate: CandidateItem) -> None:
        """接受一个 candidate 后，将其贡献累计到本 window 的 running state。"""
        item_type = candidate.item_type

        self.dedup_keys_by_type.setdefault(item_type, []).append(
            candidate.semantic_dedup_key
        )

        anchor_counts = self.anchor_counts_by_type.setdefault(item_type, {})
        anchor_id = candidate.anchor_segment_id
        anchor_counts[anchor_id] = anchor_counts.get(anchor_id, 0) + 1

        # P1-4: gate 7 跨 item_type 聚合
        self.accepted_anchors.add(anchor_id)

        if candidate.pattern_key:
            self.pattern_keys_by_type.setdefault(item_type, []).append(
                candidate.pattern_key
            )

        self.density_by_type[item_type] = (
            self.density_by_type.get(item_type, 0) + 1
        )
        self.budget_used_by_type[item_type] = (
            self.budget_used_by_type.get(item_type, 0) + 1
        )


def select_candidates(
    candidates: list[CandidateItem],
    *,
    ledger: SelectorLedger,
    window_budget: dict[str, int],
    target_anchor_ids: set[str] | None = None,
) -> SelectionResult:
    """§7.2 处理流程：8 个 hard gates 按 item_type 拆分查询。

    排序键（§7.2 step 5）：
      - quality_score desc
      - reading_blocker true first
      - sentence_analysis > grammar_note
    所有 counter 按 ``candidate.item_type`` 查询 ledger 的对应分桶。

    P1-5：维护 ``WindowRoundState`` 把当前 window 内已接受的 candidate 累计
    到后续 gate 检查上，避免同 window 内接受重复 dedup key / 同 anchor
    多 item / 超 budget / density。

    P1-3（§7.2 step 2 pre-filter）：当 ``target_anchor_ids`` 提供时，拒绝
    ``anchor_segment_id`` 不在该集合内的 candidate。防止 LLM 返回 window
    范围外的 anchor 导致非法 layer。
    """
    accepted: list[CandidateItem] = []
    rejected: list[RejectedCandidate] = []
    window_count_by_type: dict[str, int] = {"grammar_note": 0, "sentence_analysis": 0}
    window_round = WindowRoundState()

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
        # P1-3: §7.2 step 2 pre-filter — anchor_segment_id ∈ target_anchor_ids
        # Empty set is treated as None (defensive: skip pre-filter when no
        # target anchors are available, e.g. malformed window_row).
        if (
            target_anchor_ids
            and candidate.anchor_segment_id not in target_anchor_ids
        ):
            rejected.append(
                RejectedCandidate(
                    candidate=candidate,
                    gate=SelectionGate.INVALID_ANCHOR,
                    reason=(
                        f"anchor_segment_id {candidate.anchor_segment_id} "
                        f"not in target_anchor_ids"
                    ),
                )
            )
            continue

        gate_failure = _check_gates(
            candidate, ledger, window_count_by_type, window_budget, window_round
        )
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
        # P1-5：同 window 内接受的 candidate 累计到 window_round，
        # 后续 gate 检查时叠加在 ledger 之上
        window_round.add(candidate)

    return SelectionResult(accepted=accepted, rejected=rejected)


def _check_gates(
    candidate: CandidateItem,
    ledger: SelectorLedger,
    window_count_by_type: dict[str, int],
    window_budget: dict[str, int],
    window_round: WindowRoundState,
) -> tuple[SelectionGate, str] | None:
    """按顺序检查 8 个 gates，返回第一个失败的 (gate, reason)，全部通过返回 None。

    所有 counter 按 ``candidate.item_type`` 查询 ledger 的对应分桶，
    gate 7 (ANCHOR_RATIO) 跨 item_type 聚合。

    P1-5：DUP / PATTERN_DENSE / ANCHOR_CAP / RECORD_DENSITY / RECORD_BUDGET
    的 counter 在 ledger 之上叠加 ``window_round`` 的同 window 已接受累计值。
    """
    item_type = candidate.item_type

    # gate 1 (DUP): semantic_dedup_key(item_type) 已在 ledger 或当前 window 已接受
    if (
        candidate.semantic_dedup_key
        in ledger.published_dedup_keys_by_type.get(item_type, [])
        or candidate.semantic_dedup_key
        in window_round.dedup_keys_by_type.get(item_type, [])
    ):
        return (
            SelectionGate.DUP,
            f"semantic_dedup_key {candidate.semantic_dedup_key} already published for {item_type}",
        )

    # gate 2 (PATTERN_DENSE): pattern_key 出现 >= 3 次（仅 grammar_note）
    # 统计 ledger + 当前 window 已接受的累计
    if item_type == "grammar_note" and candidate.pattern_key:
        ledger_pattern_count = (
            ledger.published_pattern_keys_by_type.get(item_type, []).count(
                candidate.pattern_key
            )
        )
        window_pattern_count = (
            window_round.pattern_keys_by_type.get(item_type, []).count(
                candidate.pattern_key
            )
        )
        pattern_count = ledger_pattern_count + window_pattern_count
        if pattern_count >= PATTERN_DENSE_THRESHOLD:
            return (
                SelectionGate.PATTERN_DENSE,
                f"pattern {candidate.pattern_key} too dense ({pattern_count} >= {PATTERN_DENSE_THRESHOLD})",
            )

    # gate 3 (ANCHOR_CAP): anchor 已达 cap (per_anchor_cap=1)
    # ledger + 当前 window 已接受的累计
    ledger_anchor_count = (
        ledger.published_anchor_counts_by_type.get(item_type, {}).get(
            candidate.anchor_segment_id, 0
        )
    )
    window_anchor_count = (
        window_round.anchor_counts_by_type.get(item_type, {}).get(
            candidate.anchor_segment_id, 0
        )
    )
    anchor_count = ledger_anchor_count + window_anchor_count
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
    # §7.3 P2-6：density = total_published_count / max(base_text_length_utf16 / 1000, 1.0)
    # total_published_count = ledger.density_by_record[type] + window_round 已接受累计
    total_published_count = (
        ledger.density_by_record.get(item_type, 0)
        + window_round.density_by_type.get(item_type, 0)
    )
    base_length = ledger.base_text_length_utf16
    density_denom = max(base_length / 1000, 1.0)
    current_density = total_published_count / density_denom
    density_cap = ledger.density_cap.get(item_type, 0.0)
    if current_density >= density_cap:
        return (
            SelectionGate.RECORD_DENSITY,
            f"record {item_type} density {current_density:.4f} >= cap {density_cap} (base_len={base_length})",
        )

    # gate 6 (RECORD_BUDGET): budget_used.count >= budget_total.count
    # ledger + 当前 window 已接受的累计
    ledger_used = ledger.budget_used.get(item_type, {"count": 0}).get("count", 0)
    window_used = window_round.budget_used_by_type.get(item_type, 0)
    used = ledger_used + window_used
    total = ledger.budget_total.get(item_type, {"count": 0}).get("count", 0)
    if used >= total:
        return (
            SelectionGate.RECORD_BUDGET,
            f"record {item_type} budget {used} >= total {total}",
        )

    # gate 7 (ANCHOR_RATIO): projected annotated_anchor_ratio > 0.30
    # P1-4: 必须检查 projected ratio（含当前 candidate + 同 window 已接受），
    # 不能只看 ledger 当前值。否则同一 window 内可以接受到 40%+ ratio。
    # projected = (ledger 已有 + 同 window 已接受 + 当前 candidate 如果是新 anchor) / total
    if ledger.total_anchors > 0:
        projected_anchors = set(ledger.annotated_anchors) | window_round.accepted_anchors
        candidate_anchor_is_new = (
            candidate.anchor_segment_id not in projected_anchors
        )
        if candidate_anchor_is_new:
            projected_anchors.add(candidate.anchor_segment_id)
        projected_ratio = len(projected_anchors) / ledger.total_anchors
        if projected_ratio > ANCHOR_RATIO_THRESHOLD:
            return (
                SelectionGate.ANCHOR_RATIO,
                f"projected annotated ratio {projected_ratio:.2f} "
                f"> {ANCHOR_RATIO_THRESHOLD}",
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
