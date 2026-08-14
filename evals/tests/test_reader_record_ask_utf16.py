"""Tests for UTF-16 code unit helpers.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
spec.md` — Requirement: UTF-16 长度修正.

Covers:
- ``utf16_code_units`` returns correct count for BMP and astral-plane
  characters (emoji, surrogate pairs).
- ``slice_by_utf16`` round-trips with ``utf16_code_units``.
- ``build_unit_offsets`` produces monotonic, non-overlapping offsets
  even when units contain emoji.
- ``offsets_are_monotonic_and_non_overlapping`` validates the invariant.
- Empty units are rejected (fail-closed).

The bug being fixed: the previous harness computed UTF-16 length as
``sum(1 for _ in text.encode("utf-16-le").decode("utf-16-le"))`` which
round-trips back to a Python ``str`` and counts code points, not
UTF-16 code units. Astral-plane characters (emoji) were undercounted
by 1 each.
"""

from __future__ import annotations

import pytest

from claread_eval.reader_record_ask.utf16 import (
    UnitOffset,
    build_unit_offsets,
    offsets_are_monotonic_and_non_overlapping,
    slice_by_utf16,
    utf16_code_units,
)

# ---------------------------------------------------------------------------
# utf16_code_units — basic correctness
# ---------------------------------------------------------------------------


def test_utf16_code_units_empty_string() -> None:
    assert utf16_code_units("") == 0


def test_utf16_code_units_ascii() -> None:
    assert utf16_code_units("abc") == 3
    assert utf16_code_units("hello world") == 11


def test_utf16_code_units_bmp_cjk() -> None:
    """CJK characters in the BMP occupy 1 UTF-16 code unit each."""
    assert utf16_code_units("纽约") == 2
    assert utf16_code_units("中文测试") == 4


def test_utf16_code_units_bmp_cyrillic() -> None:
    assert utf16_code_units("Привет") == 6


def test_utf16_code_units_astral_plane_emoji() -> None:
    """Spec: "增加 emoji/non-BMP 回归".

    Astral-plane characters (code points > U+FFFF) are encoded as a
    surrogate pair in UTF-16, occupying 2 code units each.
    """
    # U+1F4A9 PILE OF POO
    assert utf16_code_units("💩") == 2
    # U+1F600 GRINNING FACE
    assert utf16_code_units("😀") == 2
    # U+1F680 ROCKET
    assert utf16_code_units("🚀") == 2


def test_utf16_code_units_mixed_ascii_and_emoji() -> None:
    """Spec: "增加 emoji/non-BMP 回归".

    'a💩b' = 1 (a) + 2 (💩) + 1 (b) = 4 UTF-16 code units.
    The previous buggy implementation would have returned 3.
    """
    assert utf16_code_units("a💩b") == 4
    # "hello " = 6 code units, "🌍" = 2 code units, " world" = 6 code units → 14
    assert utf16_code_units("hello 🌍 world") == 14


def test_utf16_code_units_astral_cjk_extension_b() -> None:
    """CJK Extension B characters are in the astral plane.

    𠀀 (U+20000) — first char of CJK Extension B.
    """
    assert utf16_code_units("𠀀") == 2
    assert utf16_code_units("a𠀀b") == 4


def test_utf16_code_units_matches_python_len_for_bmp_only() -> None:
    """For BMP-only text, UTF-16 code unit count == Python len."""
    bmp_samples = [
        "abc",
        "纽约",
        "Привет",
        "hello world",
        "中文测试",
        "café",
        "naïve",
    ]
    for sample in bmp_samples:
        assert utf16_code_units(sample) == len(sample), f"failed for {sample!r}"


def test_utf16_code_units_differs_from_python_len_for_astral() -> None:
    """For astral-plane text, UTF-16 code unit count > Python len."""
    astral_samples = ["💩", "😀", "🚀", "🌍", "𠀀"]
    for sample in astral_samples:
        assert utf16_code_units(sample) == len(sample) + 1, (
            f"failed for {sample!r}: utf16={utf16_code_units(sample)}, "
            f"len={len(sample)}"
        )


# ---------------------------------------------------------------------------
# slice_by_utf16
# ---------------------------------------------------------------------------


def test_slice_by_utf16_basic() -> None:
    text = "hello world"
    assert slice_by_utf16(text, 0, 5) == "hello"
    assert slice_by_utf16(text, 6, 11) == "world"
    assert slice_by_utf16(text, 0, 11) == text


def test_slice_by_utf16_cjk() -> None:
    text = "纽约时报"
    assert slice_by_utf16(text, 0, 2) == "纽约"
    assert slice_by_utf16(text, 2, 4) == "时报"


def test_slice_by_utf16_with_emoji() -> None:
    """Spec: "增加 emoji/non-BMP 回归".

    'a💩b' has UTF-16 offsets:
    - 'a' = [0, 1)
    - '💩' = [1, 3)  (2 code units)
    - 'b' = [3, 4)
    """
    text = "a💩b"
    assert slice_by_utf16(text, 0, 1) == "a"
    assert slice_by_utf16(text, 1, 3) == "💩"
    assert slice_by_utf16(text, 3, 4) == "b"
    assert slice_by_utf16(text, 0, 4) == text


def test_slice_by_utf16_rejects_negative_start() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        slice_by_utf16("abc", -1, 2)


def test_slice_by_utf16_rejects_negative_end() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        slice_by_utf16("abc", 0, -1)


def test_slice_by_utf16_rejects_start_greater_than_end() -> None:
    with pytest.raises(ValueError, match="start must be <= end"):
        slice_by_utf16("abc", 2, 1)


def test_slice_by_utf16_rejects_end_beyond_length() -> None:
    with pytest.raises(ValueError, match="exceeds text length"):
        slice_by_utf16("abc", 0, 10)


def test_slice_by_utf16_empty_range_returns_empty_string() -> None:
    assert slice_by_utf16("abc", 1, 1) == ""


# ---------------------------------------------------------------------------
# build_unit_offsets — monotonicity and non-overlap
# ---------------------------------------------------------------------------


def test_build_unit_offsets_empty_list_returns_empty_list() -> None:
    assert build_unit_offsets([]) == []


def test_build_unit_offsets_single_unit_starts_at_zero() -> None:
    offsets = build_unit_offsets(["hello"])
    assert len(offsets) == 1
    assert offsets[0].unit_index == 0
    assert offsets[0].start == 0
    assert offsets[0].end == 5
    assert offsets[0].length == 5


def test_build_unit_offsets_multiple_units_are_contiguous() -> None:
    offsets = build_unit_offsets(["hello", " ", "world"])
    assert len(offsets) == 3
    # unit 0: [0, 5)
    assert offsets[0].start == 0
    assert offsets[0].end == 5
    # unit 1: [5, 6)
    assert offsets[1].start == 5
    assert offsets[1].end == 6
    # unit 2: [6, 11)
    assert offsets[2].start == 6
    assert offsets[2].end == 11


def test_build_unit_offsets_with_emoji() -> None:
    """Spec: "验证 unit offsets 单调、无重叠".

    Units with emoji must produce correct offsets accounting for
    surrogate pairs.
    """
    offsets = build_unit_offsets(["a", "💩", "b"])
    assert len(offsets) == 3
    # unit 0: 'a' = [0, 1)
    assert offsets[0].start == 0
    assert offsets[0].end == 1
    # unit 1: '💩' = [1, 3)  — surrogate pair = 2 code units
    assert offsets[1].start == 1
    assert offsets[1].end == 3
    # unit 2: 'b' = [3, 4)
    assert offsets[2].start == 3
    assert offsets[2].end == 4
    # Monotonicity + non-overlap invariant
    assert offsets_are_monotonic_and_non_overlapping(offsets)


def test_build_unit_offsets_mixed_cjk_and_emoji() -> None:
    """Realistic case: CJK article with emoji sprinkled in."""
    offsets = build_unit_offsets(["纽约时报", "发布了一则", "💩", "消息"])
    assert len(offsets) == 4
    # unit 0: 4 CJK chars = 4 UTF-16 code units
    assert offsets[0].start == 0
    assert offsets[0].end == 4
    # unit 1: 5 CJK chars = 5 UTF-16 code units
    assert offsets[1].start == 4
    assert offsets[1].end == 9
    # unit 2: emoji = 2 UTF-16 code units
    assert offsets[2].start == 9
    assert offsets[2].end == 11
    # unit 3: 2 CJK chars = 2 UTF-16 code units
    assert offsets[3].start == 11
    assert offsets[3].end == 13
    assert offsets_are_monotonic_and_non_overlapping(offsets)


def test_build_unit_offsets_rejects_empty_unit() -> None:
    """Spec: "fail-closed".

    The harness must not silently produce zero-length offsets — that
    would indicate a bug in the unit splitting logic.
    """
    with pytest.raises(ValueError, match="unit\\[1\\] is empty"):
        build_unit_offsets(["hello", "", "world"])


def test_build_unit_offsets_first_unit_empty_also_rejected() -> None:
    with pytest.raises(ValueError, match="unit\\[0\\] is empty"):
        build_unit_offsets(["", "hello"])


def test_build_unit_offsets_produces_monotonic_offsets_for_long_article() -> None:
    """Realistic regression: 50 units with mixed BMP + astral content."""
    units = []
    for i in range(50):
        if i % 5 == 0:
            units.append(f"emoji-{i}-💩")
        else:
            units.append(f"unit-{i}-text")
    offsets = build_unit_offsets(units)
    assert len(offsets) == 50
    assert offsets_are_monotonic_and_non_overlapping(offsets)
    # First offset starts at 0
    assert offsets[0].start == 0
    # Last offset ends at the sum of all UTF-16 lengths
    total = sum(utf16_code_units(u) for u in units)
    assert offsets[-1].end == total


# ---------------------------------------------------------------------------
# offsets_are_monotonic_and_non_overlapping — invariant checker
# ---------------------------------------------------------------------------


def test_offsets_are_monotonic_empty_list_returns_true() -> None:
    assert offsets_are_monotonic_and_non_overlapping([])


def test_offsets_are_monotonic_single_offset() -> None:
    offsets = [UnitOffset(unit_index=0, start=0, end=5)]
    assert offsets_are_monotonic_and_non_overlapping(offsets)


def test_offsets_are_monotonic_contiguous_offsets() -> None:
    offsets = [
        UnitOffset(unit_index=0, start=0, end=5),
        UnitOffset(unit_index=1, start=5, end=10),
        UnitOffset(unit_index=2, start=10, end=15),
    ]
    assert offsets_are_monotonic_and_non_overlapping(offsets)


def test_offsets_are_monotonic_rejects_gap() -> None:
    offsets = [
        UnitOffset(unit_index=0, start=0, end=5),
        # Gap: 5 → 7 instead of 5 → 5
        UnitOffset(unit_index=1, start=7, end=10),
    ]
    assert not offsets_are_monotonic_and_non_overlapping(offsets)


def test_offsets_are_monotonic_rejects_overlap() -> None:
    offsets = [
        UnitOffset(unit_index=0, start=0, end=5),
        # Overlap: 4 < 5
        UnitOffset(unit_index=1, start=4, end=10),
    ]
    assert not offsets_are_monotonic_and_non_overlapping(offsets)


def test_offsets_are_monotonic_rejects_zero_length_unit() -> None:
    offsets = [
        UnitOffset(unit_index=0, start=0, end=0),  # zero length!
    ]
    assert not offsets_are_monotonic_and_non_overlapping(offsets)


def test_offsets_are_monotonic_rejects_first_offset_not_starting_at_zero() -> None:
    offsets = [
        UnitOffset(unit_index=0, start=5, end=10),  # doesn't start at 0
    ]
    assert not offsets_are_monotonic_and_non_overlapping(offsets)


# ---------------------------------------------------------------------------
# Round-trip: build_unit_offsets → slice_by_utf16 → original text
# ---------------------------------------------------------------------------


def test_slice_by_utf16_round_trips_with_build_unit_offsets() -> None:
    """Spec: "验证 unit offsets 单调、无重叠".

    Each unit's offsets, when used to slice the concatenated article
    text, must produce exactly the original unit text.
    """
    units = ["hello", " ", "world", "💩", "中文"]
    offsets = build_unit_offsets(units)
    # Concatenate the article text
    article = "".join(units)
    # Each unit's slice must equal the original unit
    for offset, original_unit in zip(offsets, units, strict=True):
        sliced = slice_by_utf16(article, offset.start, offset.end)
        assert sliced == original_unit


def test_slice_by_utf16_round_trip_with_long_emoji_article() -> None:
    """Stress test: 30 units with emoji every 3rd unit."""
    units = []
    for i in range(30):
        if i % 3 == 0:
            units.append(f"emoji-{i}-💩-🌍")
        else:
            units.append(f"unit-{i}")
    offsets = build_unit_offsets(units)
    article = "".join(units)
    for offset, original_unit in zip(offsets, units, strict=True):
        sliced = slice_by_utf16(article, offset.start, offset.end)
        assert sliced == original_unit
