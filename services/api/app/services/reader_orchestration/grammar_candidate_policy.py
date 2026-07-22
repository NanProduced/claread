"""Grammar candidate selection 合同的唯一来源。

这是 grammar candidate selection 合同的唯一来源，per-unit / batch / window
三路径必须 import 此模块，不得复制实现。

提供：
  - 常量：``MAX_DEDUP_HINT_LENGTH`` / ``GRAMMAR_NOTE_TYPE`` /
    ``SENTENCE_ANALYSIS_TYPE`` / ``DEDUP_HINT_DUPLICATE_REASON_CODE`` /
    ``DEDUP_WINNER_SOURCE_CURRENT_WINDOW`` /
    ``DEDUP_WINNER_SOURCE_PUBLISHED_LEDGER``
  - ``normalize_dedup_hint(hint)``：规范化 ``dedup_hint`` 用于 scoped dedup 比较
  - ``validate_dedup_hint(hint)``：trim + 非空 + 长度校验，返回 normalized hint
  - ``grammar_candidate_sort_key(...)``：三路径统一的候选排序键
  - ``scoped_dedup_key(...)``：``(anchor_segment_id, normalized_dedup_hint)``
    元组，作为 DUP gate 的去重身份；对非法 hint fail-closed

生产代码与对应测试共同构成当前合同；不依赖仓库外的过程性 spec。
"""

from __future__ import annotations

from typing import Final, Literal

MAX_DEDUP_HINT_LENGTH = 120
GRAMMAR_NOTE_TYPE = "grammar_note"
SENTENCE_ANALYSIS_TYPE = "sentence_analysis"
DEDUP_HINT_DUPLICATE_REASON_CODE = "dedup_hint_duplicate"
# reader-grammar-candidate-selection: DUP rejection 结构化 metadata 的
# winner_source 取值。current_window = 同 window 内已接受的 winner（携带
# 真实 item_index）；published_ledger = plan ledger 已发布的 winner
# （item_index 为 null，不得伪造）。
DedupWinnerSource = Literal["current_window", "published_ledger"]
DEDUP_WINNER_SOURCE_CURRENT_WINDOW: Final[DedupWinnerSource] = "current_window"
DEDUP_WINNER_SOURCE_PUBLISHED_LEDGER: Final[DedupWinnerSource] = "published_ledger"


def normalize_dedup_hint(hint: str) -> str:
    """Normalize a ``dedup_hint`` for scoped dedup comparison.

    Lowercase + collapse internal whitespace. The LLM may emit slightly
    different formatting across runs/types; normalization makes the
    dedup gate robust without losing semantic precision. Used by
    ``scoped_dedup_key`` to compute the second tuple element.

    Note: this function does NOT validate non-empty / length — callers
    that need validation should use ``validate_dedup_hint`` instead.
    """
    return " ".join(hint.lower().split())


def validate_dedup_hint(hint: str) -> str:
    """Validate and normalize a ``dedup_hint``.

    Steps:
      1. ``hint.strip()`` (trim leading/trailing whitespace)
      2. ``normalize_dedup_hint(...)`` (lowercase + collapse internal whitespace)
      3. Reject empty string (after trim) with ``ValueError``
      4. Reject strings longer than ``MAX_DEDUP_HINT_LENGTH`` with ``ValueError``
      5. Return the normalized hint

    Used at schema / candidate construction boundaries to enforce the
    non-empty / ≤120-char contract before the dedup identity is computed.
    """
    trimmed = hint.strip()
    normalized = normalize_dedup_hint(trimmed)
    if not normalized:
        raise ValueError("dedup_hint must be non-empty after trim/normalize")
    if len(normalized) > MAX_DEDUP_HINT_LENGTH:
        raise ValueError(
            f"dedup_hint length {len(normalized)} exceeds "
            f"MAX_DEDUP_HINT_LENGTH={MAX_DEDUP_HINT_LENGTH}"
        )
    return normalized


def grammar_candidate_sort_key(
    *,
    item_type: str,
    quality_score: int,
    reading_blocker: bool,
) -> tuple[int, int, int]:
    """Compute the sort key for a grammar candidate.

    Sort order (ascending tuple comparison):
      1. ``-quality_score`` (higher score first)
      2. ``0 if reading_blocker else 1`` (blocker=true before blocker=false)
      3. ``0 if item_type == GRAMMAR_NOTE_TYPE else 1``
         (grammar_note before sentence_analysis on tie — sentence_analysis
         is expected to clear a higher bar via whole-clause reading blocker)

    per-unit / batch / window 三路径 MUST 调用此函数计算排序键，MUST NOT
    在调用方文件内复制排序逻辑。
    """
    return (
        -quality_score,
        0 if reading_blocker else 1,
        0 if item_type == GRAMMAR_NOTE_TYPE else 1,
    )


def scoped_dedup_key(
    *,
    anchor_segment_id: str,
    dedup_hint: str,
) -> tuple[str, str]:
    """Compute the scoped dedup key for a candidate.

    Returns ``(anchor_segment_id, validate_dedup_hint(dedup_hint))``.

    reader-grammar-candidate-selection: fail-closed on illegal hint.
    ``validate_dedup_hint`` raises ``ValueError`` when the hint is empty
    after trim/normalize or exceeds ``MAX_DEDUP_HINT_LENGTH``. The
    Pydantic schema ``field_validator`` and ``CandidateItem.__post_init__``
    are expected to have already normalized the hint before this function
    is called, so a failure here indicates a contract violation upstream
    and must NOT be silently skipped.

    Semantic:
      - Same ``anchor_segment_id`` + same normalized ``dedup_hint`` →
        duplicate (only one survives, winner decided by sort order)
      - Different ``anchor_segment_id`` + same ``dedup_hint`` →
        NOT duplicate (same learning point on different anchors is
        allowed; full-text repetition control is delegated to
        PATTERN_DENSE / ANCHOR_CAP / RECORD_DENSITY / RECORD_BUDGET gates)
    """
    normalized_hint = validate_dedup_hint(dedup_hint)
    return (anchor_segment_id, normalized_hint)
