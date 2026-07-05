"""Expected Z+ window formation for BBC article cd6684a0.

Based on §5.2 algorithm with target_max=1500 / safety_max=3000 / context_anchor_count=2,
BBC's 37 short units should produce 3-5 windows.

Budget 来自 §7.3：
- grammar_note: min(ceil(content_chars / 1000) * 2, 18) = min(ceil(6.064) * 2, 18) = min(14, 18) = 14
- sentence_analysis: min(max(round(content_chars / 2000), 1), 5) = min(max(3, 1), 5) = 3
"""

EXPECTED_WINDOW_COUNT_MIN = 3
EXPECTED_WINDOW_COUNT_MAX = 5

# 全篇 grammar_note 上限
EXPECTED_GRAMMAR_NOTE_TOTAL_MAX = 14  # ceil(6.064) * 2 = 14
# 全篇 sentence_analysis 上限
EXPECTED_SENTENCE_ANALYSIS_TOTAL_MAX = 3  # max(round(3.032), 1) = 3

# 每个 unit 最多被一个 window 覆盖
EXPECTED_MAX_GRAMMAR_NOTE_PER_UNIT = 1
EXPECTED_MAX_SENTENCE_ANALYSIS_PER_UNIT = 1

# 每 window budget
WINDOW_BUDGET_GRAMMAR_NOTE = 2
WINDOW_BUDGET_SENTENCE_ANALYSIS = 1


def assert_expected_window_count(actual_count: int) -> None:
    """验证 BBC 文章切分后的 window 数量在预期范围内"""
    assert EXPECTED_WINDOW_COUNT_MIN <= actual_count <= EXPECTED_WINDOW_COUNT_MAX, (
        f"Window count {actual_count} outside expected range "
        f"[{EXPECTED_WINDOW_COUNT_MIN}, {EXPECTED_WINDOW_COUNT_MAX}]"
    )


def assert_expected_grammar_note_total(actual_count: int) -> None:
    """验证 BBC 文章 grammar_note 总数不超过预算"""
    assert actual_count <= EXPECTED_GRAMMAR_NOTE_TOTAL_MAX, (
        f"grammar_note total {actual_count} exceeds budget {EXPECTED_GRAMMAR_NOTE_TOTAL_MAX}"
    )


def assert_expected_sentence_analysis_total(actual_count: int) -> None:
    """验证 BBC 文章 sentence_analysis 总数不超过预算"""
    assert actual_count <= EXPECTED_SENTENCE_ANALYSIS_TOTAL_MAX, (
        f"sentence_analysis total {actual_count} exceeds budget {EXPECTED_SENTENCE_ANALYSIS_TOTAL_MAX}"
    )
