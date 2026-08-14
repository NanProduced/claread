"""Tests for ``grammar_candidate_policy`` 中立模块（）。

覆盖：
  - ``normalize_dedup_hint``：折叠空白、大小写归一化
  - ``validate_dedup_hint``：拒绝空串（含纯空白）、拒绝 >120 字符、返回 normalized hint
  - ``grammar_candidate_sort_key``：高分优先、同分 blocker 优先、同分同 blocker 时 grammar_note 优先
  - ``scoped_dedup_key``：返回 (anchor, normalized_hint) 元组；同 anchor 同 hint
    相等；不同 anchor 同 hint 不等
  - 常量值
"""

from __future__ import annotations

import pytest

from app.services.reader_orchestration.grammar_candidate_policy import (
    DEDUP_HINT_DUPLICATE_REASON_CODE,
    GRAMMAR_NOTE_TYPE,
    MAX_DEDUP_HINT_LENGTH,
    SENTENCE_ANALYSIS_TYPE,
    grammar_candidate_sort_key,
    normalize_dedup_hint,
    scoped_dedup_key,
    validate_dedup_hint,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_constants_have_expected_values():
    assert MAX_DEDUP_HINT_LENGTH == 120
    assert GRAMMAR_NOTE_TYPE == "grammar_note"
    assert SENTENCE_ANALYSIS_TYPE == "sentence_analysis"
    assert DEDUP_HINT_DUPLICATE_REASON_CODE == "dedup_hint_duplicate"


# ---------------------------------------------------------------------------
# normalize_dedup_hint
# ---------------------------------------------------------------------------


def test_normalize_dedup_hint_collapses_whitespace_and_lowercases():
    # Leading/trailing + multiple internal spaces + mixed case
    assert normalize_dedup_hint("  Foo   BAR  ") == "foo bar"


def test_normalize_dedup_hint_handles_tabs_and_newlines():
    assert normalize_dedup_hint("Foo\tBAR\n baz") == "foo bar baz"


def test_normalize_dedup_hint_returns_empty_string_for_whitespace_only():
    # normalize_dedup_hint does NOT validate non-empty — that's validate's job
    assert normalize_dedup_hint("   ") == ""


def test_normalize_dedup_hint_idempotent():
    hint = "Though Concession"
    once = normalize_dedup_hint(hint)
    twice = normalize_dedup_hint(once)
    assert once == twice == "though concession"


# ---------------------------------------------------------------------------
# validate_dedup_hint
# ---------------------------------------------------------------------------


def test_validate_dedup_hint_returns_normalized_hint():
    assert validate_dedup_hint("  Foo   BAR  ") == "foo bar"


def test_validate_dedup_hint_rejects_empty_string():
    with pytest.raises(ValueError):
        validate_dedup_hint("")


def test_validate_dedup_hint_rejects_whitespace_only_string():
    with pytest.raises(ValueError):
        validate_dedup_hint("    \t  \n  ")


def test_validate_dedup_hint_rejects_string_longer_than_max():
    # 121 chars after normalization (single token, no whitespace to collapse)
    too_long = "a" * (MAX_DEDUP_HINT_LENGTH + 1)
    with pytest.raises(ValueError):
        validate_dedup_hint(too_long)


def test_validate_dedup_hint_accepts_string_at_max_length():
    exactly_max = "a" * MAX_DEDUP_HINT_LENGTH
    assert validate_dedup_hint(exactly_max) == exactly_max


def test_validate_dedup_hint_trims_before_validation():
    # After trim, this is empty — should raise
    with pytest.raises(ValueError):
        validate_dedup_hint("   \n\t  ")


# ---------------------------------------------------------------------------
# grammar_candidate_sort_key
# ---------------------------------------------------------------------------


def test_sort_key_higher_quality_score_first():
    key_high = grammar_candidate_sort_key(
        item_type=GRAMMAR_NOTE_TYPE, quality_score=5, reading_blocker=False
    )
    key_low = grammar_candidate_sort_key(
        item_type=GRAMMAR_NOTE_TYPE, quality_score=1, reading_blocker=False
    )
    assert key_high < key_low  # ascending → higher score comes first


def test_sort_key_reading_blocker_first_on_same_score():
    key_blocker = grammar_candidate_sort_key(
        item_type=GRAMMAR_NOTE_TYPE, quality_score=3, reading_blocker=True
    )
    key_non_blocker = grammar_candidate_sort_key(
        item_type=GRAMMAR_NOTE_TYPE, quality_score=3, reading_blocker=False
    )
    assert key_blocker < key_non_blocker  # blocker before non-blocker


def test_sort_key_grammar_note_before_sentence_analysis_on_tie():
    key_grammar = grammar_candidate_sort_key(
        item_type=GRAMMAR_NOTE_TYPE, quality_score=3, reading_blocker=False
    )
    key_sentence = grammar_candidate_sort_key(
        item_type=SENTENCE_ANALYSIS_TYPE, quality_score=3, reading_blocker=False
    )
    assert key_grammar < key_sentence  # grammar_note before sentence_analysis


def test_sort_key_quality_score_beats_blocker_flag():
    # Higher score non-blocker should still beat lower score blocker
    key_high_non_blocker = grammar_candidate_sort_key(
        item_type=GRAMMAR_NOTE_TYPE, quality_score=5, reading_blocker=False
    )
    key_low_blocker = grammar_candidate_sort_key(
        item_type=GRAMMAR_NOTE_TYPE, quality_score=1, reading_blocker=True
    )
    assert key_high_non_blocker < key_low_blocker


def test_sort_key_returns_three_element_tuple():
    key = grammar_candidate_sort_key(
        item_type=GRAMMAR_NOTE_TYPE, quality_score=3, reading_blocker=True
    )
    assert isinstance(key, tuple)
    assert len(key) == 3
    assert key == (-3, 0, 0)


# ---------------------------------------------------------------------------
# scoped_dedup_key
# ---------------------------------------------------------------------------


def test_scoped_dedup_key_returns_tuple_of_anchor_and_normalized_hint():
    key = scoped_dedup_key(
        anchor_segment_id="a1", dedup_hint="  Foo   BAR  "
    )
    assert key == ("a1", "foo bar")


def test_scoped_dedup_key_same_anchor_same_hint_are_equal():
    k1 = scoped_dedup_key(anchor_segment_id="a1", dedup_hint="Though Concession")
    k2 = scoped_dedup_key(anchor_segment_id="a1", dedup_hint="  though   concession  ")
    assert k1 == k2 == ("a1", "though concession")


def test_scoped_dedup_key_different_anchor_same_hint_not_equal():
    k1 = scoped_dedup_key(anchor_segment_id="a1", dedup_hint="though_concession")
    k2 = scoped_dedup_key(anchor_segment_id="a2", dedup_hint="though_concession")
    assert k1 != k2
    assert k1[1] == k2[1]  # normalized hint is the same
    assert k1[0] != k2[0]  # anchor differs


def test_scoped_dedup_key_same_anchor_different_hint_not_equal():
    k1 = scoped_dedup_key(anchor_segment_id="a1", dedup_hint="hint_a")
    k2 = scoped_dedup_key(anchor_segment_id="a1", dedup_hint="hint_b")
    assert k1 != k2


# ---------------------------------------------------------------------------
# reader-grammar-candidate-selection: scoped_dedup_key fail-closed
# ---------------------------------------------------------------------------


def test_scoped_dedup_key_rejects_empty_hint():
    """reader-grammar-candidate-selection: empty hint must fail-closed."""
    with pytest.raises(ValueError):
        scoped_dedup_key(anchor_segment_id="a1", dedup_hint="")


def test_scoped_dedup_key_rejects_whitespace_only_hint():
    """reader-grammar-candidate-selection: whitespace-only hint must fail-closed."""
    with pytest.raises(ValueError):
        scoped_dedup_key(anchor_segment_id="a1", dedup_hint="   \t\n  ")


def test_scoped_dedup_key_rejects_overlong_hint():
    """reader-grammar-candidate-selection: hint > MAX_DEDUP_HINT_LENGTH must fail-closed."""
    with pytest.raises(ValueError):
        scoped_dedup_key(
            anchor_segment_id="a1",
            dedup_hint="x" * (MAX_DEDUP_HINT_LENGTH + 1),
        )


def test_scoped_dedup_key_winner_source_constants():
    """reader-grammar-candidate-selection: winner_source constants exist."""
    from app.services.reader_orchestration.grammar_candidate_policy import (
        DEDUP_WINNER_SOURCE_CURRENT_WINDOW,
        DEDUP_WINNER_SOURCE_PUBLISHED_LEDGER,
    )

    assert DEDUP_WINNER_SOURCE_CURRENT_WINDOW == "current_window"
    assert DEDUP_WINNER_SOURCE_PUBLISHED_LEDGER == "published_ledger"
