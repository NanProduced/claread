"""UTF-16 code unit helpers for the evaluation harness.

Requirement: UTF-16 长度修正.

The previous harness computed UTF-16 length as::

    return sum(1 for _ in text.encode("utf-16-le").decode("utf-16-le"))

This is WRONG — ``encode().decode()`` round-trips to the same Python
``str``, so the iteration counts Python code points, not UTF-16 code
units. Astral-plane characters (emoji, ancient scripts, some CJK
extension blocks) are represented as a single Python code point but
TWO UTF-16 code units (a surrogate pair).

This module provides the correct implementation::

    return len(text.encode("utf-16-le")) // 2

Plus a helper for slicing by UTF-16 offsets and a helper for building
monotonic, non-overlapping unit offsets — the harness uses these to
populate ``ReadingUnitView.base_start_utf16`` /
``base_end_utf16`` so the runtime's read_range tool can correctly
locate text in articles that contain emoji or other non-BMP characters.

Sibling module: ``evals/claread_eval/graders/vocabulary.py`` has a
private ``_utf16_code_units`` — that implementation is correct, but
it's private to the vocabulary grader. This module exposes the same
contract publicly for the harness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Error raised by :func:`build_unit_offsets` when the produced offsets
#: are not monotonic or overlap. This indicates a bug in the caller's
#: unit splitting (e.g. units share text, or were re-ordered after
#: splitting). Fail-closed: the harness must not emit ReadingUnitView
#: records with broken offsets.
_UTF16_SURROGATEPASS: Final[str] = "surrogatepass"


def utf16_code_units(text: str) -> int:
    """Return the length of ``text`` in UTF-16 code units.

    Correctly handles astral-plane characters (emoji, ancient scripts,
    CJK extension blocks) — each surrogate pair counts as 2 code units,
    not 1.

    Examples::

        >>> utf16_code_units("abc")
        3
        >>> utf16_code_units("纽约")
        2
        >>> utf16_code_units("💩")
        2
        >>> utf16_code_units("a💩b")
        4
        >>> utf16_code_units("")
        0
    """
    if not text:
        return 0
    return len(text.encode("utf-16-le", _UTF16_SURROGATEPASS)) // 2


def slice_by_utf16(text: str, start: int, end: int) -> str:
    """Slice ``text`` by UTF-16 code unit offsets.

    ``start`` and ``end`` are in UTF-16 code units (BMU / JS-compatible
    offsets). Returns the substring of ``text`` spanning
    ``[start, end)`` in UTF-16 space.

    Raises:
        ValueError: if ``start`` or ``end`` is negative, or
            ``start > end``, or the offsets exceed the text length.
    """
    if start < 0 or end < 0:
        raise ValueError(
            f"start and end must be non-negative, got start={start} end={end}"
        )
    if start > end:
        raise ValueError(
            f"start must be <= end, got start={start} end={end}"
        )
    encoded = text.encode("utf-16-le", _UTF16_SURROGATEPASS)
    total_units = len(encoded) // 2
    if end > total_units:
        raise ValueError(
            f"end={end} exceeds text length {total_units} in UTF-16 code units"
        )
    return encoded[start * 2 : end * 2].decode("utf-16-le", _UTF16_SURROGATEPASS)


@dataclass(frozen=True)
class UnitOffset:
    """Start/end offsets of one unit in UTF-16 code units.

    Invariants (enforced by :func:`build_unit_offsets`):

    - ``start < end`` (non-empty unit)
    - For consecutive units ``i`` and ``i+1``:
      ``units[i].end == units[i+1].start`` (no gap, no overlap)
    - ``start`` and ``end`` are monotonically non-decreasing across
      the list.
    """

    unit_index: int
    start: int
    end: int

    @property
    def length(self) -> int:
        """Length of this unit in UTF-16 code units."""
        return self.end - self.start


def build_unit_offsets(units: list[str]) -> list[UnitOffset]:
    """Build monotonic, non-overlapping UTF-16 offsets for ``units``.

    The harness calls this when constructing
    :class:`ReadingUnitView` records: each unit's
    ``base_start_utf16`` / ``base_end_utf16`` must point into the
    concatenated article text correctly, even when units contain
    emoji or other non-BMP characters.

    Invariants enforced (fail-closed):

    - Empty units list → empty offsets list (no error).
    - Each unit's offsets are non-empty (``start < end``); an empty
      unit string raises :class:`ValueError`.
    - Consecutive units share an exact boundary (no gap, no overlap).
    - Offsets are strictly monotonically increasing.

    Args:
        units: list of unit texts in article order. Empty strings are
            rejected — the caller must strip them before calling.

    Returns:
        List of :class:`UnitOffset` records, one per input unit.

    Raises:
        ValueError: if any unit is empty.
    """
    offsets: list[UnitOffset] = []
    cursor = 0
    for index, text in enumerate(units):
        if not text:
            raise ValueError(
                f"unit[{index}] is empty; build_unit_offsets requires non-empty units"
            )
        length = utf16_code_units(text)
        if length == 0:
            # Defensive: utf16_code_units returns 0 only for empty string,
            # which we already rejected above. Keep this branch explicit.
            raise ValueError(
                f"unit[{index}] has zero UTF-16 length; cannot build offsets"
            )
        start = cursor
        end = cursor + length
        offsets.append(UnitOffset(unit_index=index, start=start, end=end))
        cursor = end
    return offsets


def offsets_are_monotonic_and_non_overlapping(
    offsets: list[UnitOffset],
) -> bool:
    """Return ``True`` if ``offsets`` satisfy the monotonicity contract.

    Used by tests to verify the invariant. Production callers should
    rely on :func:`build_unit_offsets` to enforce this; this helper
    exists for assertions and runtime sanity checks.
    """
    if not offsets:
        return True
    for index, offset in enumerate(offsets):
        if offset.start >= offset.end:
            return False
        if index == 0:
            if offset.start != 0:
                return False
        else:
            prev = offsets[index - 1]
            if offset.start != prev.end:
                return False
    return True
