"""UTF-16 offset utilities for RenderScene projection.

CanonicalSpan uses Python-side (code point) offsets relative to render_text.
RenderScene anchors use UTF-16 code unit offsets (sentence-local).
This module provides conversion and validation helpers.
"""

from __future__ import annotations


def python_offset_to_utf16(text: str, python_offset: int) -> int:
    """Convert a Python-side (code point) offset to a UTF-16 code unit offset.

    For BMP characters (U+0000..U+FFFF), 1 code point = 1 UTF-16 code unit.
    For supplementary characters (U+10000+), 1 code point = 2 UTF-16 code units (surrogate pair).
    """
    prefix = text[:python_offset]
    return len(prefix.encode("utf-16-le")) // 2


def utf16_slice_text(text: str, utf16_start: int, utf16_end: int) -> str:
    """Slice text by UTF-16 code unit offsets.

    Returns the substring corresponding to text[utf16_start:utf16_end] in UTF-16 space.
    """
    encoded = text.encode("utf-16-le")
    byte_start = utf16_start * 2
    byte_end = utf16_end * 2
    if byte_start < 0 or byte_end > len(encoded) or byte_start > byte_end:
        return ""
    return encoded[byte_start:byte_end].decode("utf-16-le", errors="replace")


def validate_utf16_range(
    sentence_text: str,
    utf16_start: int,
    utf16_end: int,
    expected_text: str,
) -> bool:
    """Validate that UTF-16 slicing produces the expected text.

    Returns True if sentence_text[utf16_start:utf16_end] == expected_text.
    Returns False if the slice doesn't match or offsets are out of bounds.
    """
    encoded = sentence_text.encode("utf-16-le")
    total_units = len(encoded) // 2
    if utf16_start < 0 or utf16_end > total_units or utf16_start >= utf16_end:
        return False
    byte_start = utf16_start * 2
    byte_end = utf16_end * 2
    try:
        sliced = encoded[byte_start:byte_end].decode("utf-16-le")
    except UnicodeDecodeError:
        return False
    return sliced == expected_text


def python_range_to_utf16_range(
    render_text: str,
    sentence_text: str,
    sentence_start_in_render: int,
    python_start: int,
    python_end: int,
    expected_text: str,
) -> tuple[int, int] | None:
    """Convert a Python-side (render_text-relative) range to sentence-local UTF-16 range.

    Args:
        render_text: Full render text.
        sentence_text: The sentence's text.
        sentence_start_in_render: Python offset of sentence start in render_text.
        python_start: Python offset of range start in render_text.
        python_end: Python offset of range end in render_text.
        expected_text: The expected text at render_text[python_start:python_end].

    Returns:
        (utf16_start, utf16_end) sentence-local, or None if validation fails.
    """
    # Verify the text matches
    actual = render_text[python_start:python_end]
    if actual != expected_text:
        return None

    # Convert to sentence-local Python offsets
    local_start = python_start - sentence_start_in_render
    local_end = python_end - sentence_start_in_render

    # Verify sentence-local bounds
    if local_start < 0 or local_end > len(sentence_text):
        return None

    # Convert to UTF-16
    utf16_start = python_offset_to_utf16(sentence_text, local_start)
    utf16_end = python_offset_to_utf16(sentence_text, local_end)

    # Validate
    if not validate_utf16_range(sentence_text, utf16_start, utf16_end, expected_text):
        return None

    return utf16_start, utf16_end
