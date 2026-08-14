"""Window selector: grammar-window analysis candidate hard gates.

设计来源：docs/architecture/reader-orchestration.md
  - §7.1 Dedup Key 设计（两层 + 按 item_type 拆分）
  - §7.2 处理流程（8 个 hard gates，按 item_type 拆分查询）
  - §7.3 Hard Gates 数值

核心约束：
  - grammar_note / sentence_analysis 各自独立 typed counters（quota / density）
  - gate 7 (ANCHOR_RATIO) 跨 item_type 聚合
  - gate 2 (PATTERN_DENSE) 仅对 grammar_note 生效
  - 排序：quality_score desc → reading_blocker true first → grammar_note > sentence_analysis
    （同分时 grammar_note 优先；sentence_analysis 应有更高准入门槛）
  - DUP gate：scoped dedup key `(anchor_segment_id, normalized_dedup_hint)`
    跨 grammar_note / sentence_analysis 共享；同 anchor 同 hint 才淘汰；
    不同 anchor 同 hint 不淘汰（全文重复控制交给 pattern/density/budget gates）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.services.reader_orchestration.grammar_candidate_policy import (
    DEDUP_HINT_DUPLICATE_REASON_CODE,
    DEDUP_WINNER_SOURCE_CURRENT_WINDOW,
    DEDUP_WINNER_SOURCE_PUBLISHED_LEDGER,
    GRAMMAR_NOTE_TYPE,
    SENTENCE_ANALYSIS_TYPE,
    DedupWinnerSource,
    grammar_candidate_sort_key,
    normalize_dedup_hint,
    scoped_dedup_key,
    validate_dedup_hint,
)

# Re-export for backward compat: downstream modules import these literals
# from window_selector (e.g. publisher). The single source of truth is
# grammar_candidate_policy — window_selector just re-exports here.
__all__ = [
    "DEDUP_HINT_DUPLICATE_REASON_CODE",
    "DEDUP_WINNER_SOURCE_CURRENT_WINDOW",
    "DEDUP_WINNER_SOURCE_PUBLISHED_LEDGER",
    "GRAMMAR_NOTE_TYPE",
    "SENTENCE_ANALYSIS_TYPE",
    "CandidateItem",
    "DedupRejectionMetadata",
    "RejectedCandidate",
    "SelectionGate",
    "SelectionResult",
    "SelectorLedger",
    "WindowRoundState",
    "select_candidates",
    "normalize_dedup_hint",
    "scoped_dedup_key",
    "validate_dedup_hint",
    "grammar_candidate_sort_key",
    "PER_ANCHOR_CAP",
    "PATTERN_DENSE_THRESHOLD",
    "ANCHOR_RATIO_THRESHOLD",
]


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

# NOTE: ``GRAMMAR_NOTE_TYPE`` / ``SENTENCE_ANALYSIS_TYPE`` are imported
# from ``grammar_candidate_policy`` at the top of this module. They live
# in this module's namespace as re-exports so downstream imports such as
# ``from window_selector import GRAMMAR_NOTE_TYPE`` continue to work.


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
    published_dedup_keys_by_type: dict[str, list[tuple[str, str]]] = field(
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
    # 旧实现误把它当作 raw count， 修正为 per-1000-chars ratio。
    # Sentence_analysis 从 1.0 提升到 2.0，与 grammar_note 的 3.0
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

     self-rating contract: ``dedup_hint`` 是 LLM 产出的稳定学习点短键
    （非空，≤120 字符），DUP gate 把它规范化后跨 grammar_note /
    sentence_analysis 共享。``semantic_dedup_key`` 仍由 executor 基于
    (grammar_point, dedup_hint) / (label, dedup_hint) 计算，仅用于
    publisher 把 CandidateItem 与 WindowCandidateContent 匹配。

    reader-grammar-candidate-selection: ``__post_init__`` 真实校验三件套：
      - ``quality_score`` 必须是精确 ``int`` 1–5（拒绝 ``bool`` / ``float``；
        ``bool`` 虽是 ``int`` 子类但 ``type(x) is not int`` 即拒绝）
      - ``reading_blocker`` 必须是 ``bool``
      - ``dedup_hint`` 调 ``validate_dedup_hint`` 校验非空/≤120，并写回
        normalized 值（frozen dataclass 用 ``object.__setattr__``）
    """

    item_type: str  # "grammar_note" | "sentence_analysis"
    anchor_segment_id: str
    spans: list[dict[str, Any]]
    semantic_dedup_key: str
    pattern_key: str | None
    quality_score: int
    reading_blocker: bool
    dedup_hint: str
    # Layer content fields (bridge: executor → publisher)
    grammar_point: str = ""
    pattern: str | None = None
    note: str = ""
    label: str = ""
    analysis: str = ""
    chunks: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # reader-grammar-candidate-selection: 真实校验边界。
        # ``bool`` 是 ``int`` 子类，``isinstance(True, int)`` 为 True，
        # 所以必须用 ``type(x) is not int`` 拒绝 ``bool``；同理拒绝 ``float``。
        if type(self.quality_score) is not int:
            raise TypeError(
                f"quality_score must be exact int, got "
                f"{type(self.quality_score).__name__}"
            )
        if not 1 <= self.quality_score <= 5:
            raise ValueError(
                f"quality_score must be in 1..5, got {self.quality_score}"
            )
        if type(self.reading_blocker) is not bool:
            raise TypeError(
                f"reading_blocker must be bool, got "
                f"{type(self.reading_blocker).__name__}"
            )
        # validate_dedup_hint trims + normalizes + enforces non-empty/≤120.
        # Write the normalized value back so downstream scoped_dedup_key
        # receives an already-normalized hint (idempotent).
        normalized_hint = validate_dedup_hint(self.dedup_hint)
        object.__setattr__(self, "dedup_hint", normalized_hint)


@dataclass(frozen=True, slots=True)
class DedupRejectionMetadata:
    """reader-grammar-candidate-selection: DUP rejection 结构化 metadata。

    Publisher 直接聚合这些字段，不从 ``RejectedCandidate.reason`` 字符串
    解析。``reason_code`` 存放在 ``RejectedCandidate.reason_code``（独立字段），
    不再由 ``reason`` 字符串承担；本结构仅承载 winner 诊断信息。

    ``winner_item_index`` 合同：
      - ``winner_source == current_window`` → 真实 ``int``（winner 在
        ``sorted_candidates`` 中的位置）
      - ``winner_source == published_ledger`` → ``None``（ledger 不记录
        原始候选位置，不得伪造）
    """

    normalized_hint: str
    winner_item_type: str
    winner_anchor_segment_id: str
    winner_item_index: int | None
    winner_source: DedupWinnerSource


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    """Selector 淘汰记录。

    reader-grammar-candidate-selection: ``reason_code`` 为独立结构化字段，
    与人类可读的 ``reason`` 分离。DUP gate 设置
    ``reason_code = DEDUP_HINT_DUPLICATE_REASON_CODE``；其他 gate 的
    ``reason_code`` 为 ``None``。``reason`` 仅保留人类可读详情，不再
    承担 code 合同。publisher 的 ``rejected_breakdown`` 输出独立
    ``reason_code``，不从 ``reason`` 解析。
    """

    candidate: CandidateItem
    gate: SelectionGate
    reason: str
    # reader-grammar-candidate-selection: 独立结构化 reason_code。
    # 仅 DUP gate 设置为 DEDUP_HINT_DUPLICATE_REASON_CODE；其他 gate
    # 保持 None。publisher 的 ``_aggregate_rejected`` 直接输出此字段。
    reason_code: str | None = None
    # reader-grammar-candidate-selection: 可选结构化 dedup metadata。
    # 仅 DUP gate 填充；其他 gate 保持 None。publisher 的
    # ``_aggregate_rejected`` 直接读取这些字段，不从 reason 字符串解析。
    dedup_metadata: DedupRejectionMetadata | None = None


@dataclass(frozen=True, slots=True)
class SelectionResult:
    accepted: list[CandidateItem]
    rejected: list[RejectedCandidate]


@dataclass(slots=True)
class WindowRoundState:
    """当前 window 内已接受 candidate 的累计贡献（）。

    旧实现只读 ``ledger`` 的 pre-existing 状态，未把同一 window 内已接受的
    candidate 累计到后续 gate 检查上，导致：
      - 同一 window 内接受重复 ``semantic_dedup_key``
      - 同一 anchor 接受多个同 item_type item
      - 超过 record budget / density

    本类在每个 candidate 被接受后追加其贡献，``_check_gates`` 在检查
    DUP / PATTERN_DENSE / ANCHOR_CAP / RECORD_DENSITY / RECORD_BUDGET 时
    把这些值叠加到 ``ledger`` 之上。
    """

    dedup_keys_by_type: dict[str, dict[tuple[str, str], int]] = field(
        default_factory=lambda: {
            "grammar_note": {},
            "sentence_analysis": {},
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
    # 跨 item_type 的已接受 anchor 集合（gate 7 ANCHOR_RATIO 用）。
    # gate 7 是跨 item_type 聚合的，所以不能用 anchor_counts_by_type，
    # 需要独立的 set 追踪所有 item_type 接受过的 anchor。
    accepted_anchors: set[str] = field(default_factory=set)

    def add(self, candidate: CandidateItem) -> None:
        """接受一个 candidate 后，将其贡献累计到本 window 的 running state。

         reader-grammar-candidate-selection: ``dedup_keys_by_type``
        现在存储 scoped dedup key 元组 ``(anchor_segment_id,
        normalized_dedup_hint)`` → 出现次数。DUP gate 用此结构做
        scoped 比较（同 anchor 同 hint 才淘汰；不同 anchor 同 hint 不淘汰）。

        reader-grammar-candidate-selection: ``CandidateItem.__post_init__``
        已强制 ``dedup_hint`` 非空且 normalized，因此 ``scoped_dedup_key``
        不会返回空 hint。旧实现的 ``if scoped_key[1]:`` 静默 skip 分支
        已删除（合同已强制非空，留 skip 会掩盖上游违约）。
        """
        item_type = candidate.item_type

        scoped_key = scoped_dedup_key(
            anchor_segment_id=candidate.anchor_segment_id,
            dedup_hint=candidate.dedup_hint,
        )
        bucket = self.dedup_keys_by_type.setdefault(item_type, {})
        bucket[scoped_key] = bucket.get(scoped_key, 0) + 1

        anchor_counts = self.anchor_counts_by_type.setdefault(item_type, {})
        anchor_id = candidate.anchor_segment_id
        anchor_counts[anchor_id] = anchor_counts.get(anchor_id, 0) + 1

        # Gate 7 跨 item_type 聚合
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
      - grammar_note > sentence_analysis
        （同分时 grammar_note 优先；sentence_analysis 应有更高准入门槛）
    所有 counter 按 ``candidate.item_type`` 查询 ledger 的对应分桶。

    ：维护 ``WindowRoundState`` 把当前 window 内已接受的 candidate 累计
    到后续 gate 检查上，避免同 window 内接受重复 dedup key / 同 anchor
    多 item / 超 budget / density。

    （§7.2 step 2 pre-filter）：当 ``target_anchor_ids`` 提供时，拒绝
    ``anchor_segment_id`` 不在该集合内的 candidate。防止 LLM 返回 window
    范围外的 anchor 导致非法 layer。

     self-rating contract: DUP gate 使用 scoped dedup key
    ``(anchor_segment_id, normalize_dedup_hint(dedup_hint))`` 跨
    grammar_note / sentence_analysis 共享。同 anchor 同 hint 才淘汰
    （winner 由 sort order 决定）；不同 anchor 同 hint 不淘汰，全文重复
    控制交给 PATTERN_DENSE / ANCHOR_CAP / RECORD_DENSITY / RECORD_BUDGET。

    reader-grammar-candidate-selection: DUP rejection 携带结构化
    ``DedupRejectionMetadata``。current_window winner 记录其在
    ``sorted_candidates`` 中的真实 index；published_ledger winner 的
    index 为 ``None``（不得伪造）。
    """
    accepted: list[CandidateItem] = []
    rejected: list[RejectedCandidate] = []
    window_count_by_type: dict[str, int] = {"grammar_note": 0, "sentence_analysis": 0}
    window_round = WindowRoundState()
    # reader-grammar-candidate-selection: current_window winner 跟踪。
    # scoped_key → (winner_item_type, winner_anchor_segment_id,
    # winner_sorted_index)。仅在 candidate 被接受时记录；DUP gate 命中
    # 同 window 已接受项时读取此 map 构造 DedupRejectionMetadata。
    current_window_winners: dict[tuple[str, str], tuple[str, str, int]] = {}

    sorted_candidates = sorted(
        candidates,
        key=lambda c: grammar_candidate_sort_key(
            item_type=c.item_type,
            quality_score=c.quality_score,
            reading_blocker=c.reading_blocker,
        ),
    )

    for sorted_index, candidate in enumerate(sorted_candidates):
        # §7.2 step 2 pre-filter — anchor_segment_id ∈ target_anchor_ids
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
            candidate,
            ledger,
            window_count_by_type,
            window_budget,
            window_round,
            current_window_winners,
        )
        if gate_failure is not None:
            gate, reason, reason_code, dedup_metadata = gate_failure
            rejected.append(
                RejectedCandidate(
                    candidate=candidate,
                    gate=gate,
                    reason=reason,
                    reason_code=reason_code,
                    dedup_metadata=dedup_metadata,
                )
            )
            continue
        accepted.append(candidate)
        window_count_by_type[candidate.item_type] = (
            window_count_by_type.get(candidate.item_type, 0) + 1
        )
        # ：同 window 内接受的 candidate 累计到 window_round，
        # 后续 gate 检查时叠加在 ledger 之上
        window_round.add(candidate)
        # reader-grammar-candidate-selection: 记录 current_window winner
        # 的 (item_type, anchor_segment_id, sorted_index)，供后续同 scoped_key
        # 的 DUP rejection 构造结构化 metadata。仅记录首个（winner）。
        scoped_key = scoped_dedup_key(
            anchor_segment_id=candidate.anchor_segment_id,
            dedup_hint=candidate.dedup_hint,
        )
        if scoped_key not in current_window_winners:
            current_window_winners[scoped_key] = (
                candidate.item_type,
                candidate.anchor_segment_id,
                sorted_index,
            )

    return SelectionResult(accepted=accepted, rejected=rejected)


def _check_gates(
    candidate: CandidateItem,
    ledger: SelectorLedger,
    window_count_by_type: dict[str, int],
    window_budget: dict[str, int],
    window_round: WindowRoundState,
    current_window_winners: dict[tuple[str, str], tuple[str, str, int]],
) -> tuple[SelectionGate, str, str | None, DedupRejectionMetadata | None] | None:
    """按顺序检查 8 个 gates，返回第一个失败的
    ``(gate, reason, reason_code, dedup_metadata)``，全部通过返回 None。

    所有 counter 按 ``candidate.item_type`` 查询 ledger 的对应分桶，
    gate 7 (ANCHOR_RATIO) 跨 item_type 聚合。

    reader-grammar-candidate-selection: DUP gate 改为 scoped 比较
    ``(anchor_segment_id, normalize_dedup_hint(dedup_hint))`` 元组。
    ``published_dedup_keys_by_type`` 仍按 item_type 分桶存储（list of 元组），
    ``dedup_keys_by_type`` 也按 item_type 分桶存储（dict of 元组 → 计数）。
    DUP 检查时遍历所有 item_type 的桶，使同 anchor 同 hint 跨类型只保留
    一个候选（winner 由 sort order 决定）；不同 anchor 同 hint 不淘汰。

    DUP rejection 携带 ``DedupRejectionMetadata`` 并设置独立
    ``reason_code = DEDUP_HINT_DUPLICATE_REASON_CODE``：
      - published_ledger winner：``winner_item_index=None``（ledger 不记录
        原始候选位置，不得伪造）
      - current_window winner：``winner_item_index`` 为 winner 在
        ``sorted_candidates`` 中的真实 index
    其他 gate 的 ``reason_code`` 为 ``None``；``reason`` 仅保留人类可读
    详情，不再承担 code 合同。

    ：DUP / PATTERN_DENSE ANCHOR_CAP / RECORD_DENSITY RECORD_BUDGET
    的 counter 在 ledger 之上叠加 ``window_round`` 的同 window 已接受累计值。
    """
    item_type = candidate.item_type

    # gate 1 (DUP): scoped dedup key (anchor_segment_id,
    # normalize_dedup_hint(dedup_hint)) 跨 item_type 已在 ledger 或
    # 当前 window 已接受。reader-grammar-candidate-selection contract:
    # 同 anchor 同 hint 跨 grammar_note / sentence_analysis 默认只保留
    # 一个候选（winner 由 sort order 决定：quality_score desc →
    # reading_blocker true first → grammar_note 优先）；不同 anchor 同
    # hint 不淘汰。被淘汰的候选通过 RejectedCandidate 通道写入 diagnostic，
    # reason_code 为 DEDUP_HINT_DUPLICATE_REASON_CODE。
    #
    # CandidateItem.__post_init__ 已强制 dedup_hint 非空且 normalized，
    # scoped_dedup_key fail-closed，因此无需 ``if candidate_key[1]:`` 守卫。
    candidate_key = scoped_dedup_key(
        anchor_segment_id=candidate.anchor_segment_id,
        dedup_hint=candidate.dedup_hint,
    )
    for bucket_type in (GRAMMAR_NOTE_TYPE, SENTENCE_ANALYSIS_TYPE):
        if candidate_key in ledger.published_dedup_keys_by_type.get(
            bucket_type, []
        ):
            metadata = DedupRejectionMetadata(
                normalized_hint=candidate_key[1],
                winner_item_type=bucket_type,
                winner_anchor_segment_id=candidate_key[0],
                winner_item_index=None,
                winner_source=DEDUP_WINNER_SOURCE_PUBLISHED_LEDGER,
            )
            return (
                SelectionGate.DUP,
                f"{candidate_key[0]}/{candidate_key[1]} "
                f"already published in ledger",
                DEDUP_HINT_DUPLICATE_REASON_CODE,
                metadata,
            )
        if candidate_key in window_round.dedup_keys_by_type.get(
            bucket_type, {}
        ).keys():
            winner_item_type, winner_anchor, winner_idx = (
                current_window_winners[candidate_key]
            )
            metadata = DedupRejectionMetadata(
                normalized_hint=candidate_key[1],
                winner_item_type=winner_item_type,
                winner_anchor_segment_id=winner_anchor,
                winner_item_index=winner_idx,
                winner_source=DEDUP_WINNER_SOURCE_CURRENT_WINDOW,
            )
            return (
                SelectionGate.DUP,
                f"{candidate_key[0]}/{candidate_key[1]} "
                f"already accepted in current window",
                DEDUP_HINT_DUPLICATE_REASON_CODE,
                metadata,
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
                None,
                None,
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
                None,
                None,
            )

    # gate 4 (WINDOW_CAP): 当前 window 已发布 count >= window_budget
    if window_count_by_type.get(item_type, 0) >= window_budget.get(item_type, 0):
        return (
                SelectionGate.WINDOW_CAP,
                f"window {item_type} budget exhausted ({window_count_by_type.get(item_type, 0)} >= {window_budget.get(item_type, 0)})",
                None,
                None,
            )

    # gate 5 (RECORD_DENSITY): record density >= density_cap
    # §7.3 ：density = total_published_count / max(base_text_length_utf16 1000, 1.0)
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
                None,
                None,
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
                None,
                None,
            )

    # gate 7 (ANCHOR_RATIO): projected annotated_anchor_ratio > 0.30
    # 必须检查 projected ratio（含当前 candidate + 同 window 已接受），
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
                None,
                None,
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
            None,
            None,
        )

    return None
