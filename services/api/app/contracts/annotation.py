"""Annotation anchor contract constants and UTF-16 helpers."""

from __future__ import annotations

TEXT_RANGE_OFFSET_UNIT = "utf16"
TEXT_RANGE_HASH_ALGORITHM = "fnv1a32-utf16"


def utf16_code_unit_length(text: str) -> int:
    """Return JavaScript string length for cross-client text_range offsets."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _python_index_from_utf16_offset(text: str, offset: int) -> int | None:
    if offset < 0:
        return None
    current = 0
    for index, char in enumerate(text):
        if current == offset:
            return index
        current += 2 if ord(char) > 0xFFFF else 1
        if current > offset:
            return None
    return len(text) if current == offset else None


def slice_by_utf16_offsets(text: str, start_offset: int, end_offset: int) -> str | None:
    start_index = _python_index_from_utf16_offset(text, start_offset)
    end_index = _python_index_from_utf16_offset(text, end_offset)
    if start_index is None or end_index is None or start_index >= end_index:
        return None
    return text[start_index:end_index]


def compute_text_range_hash(text: str) -> str:
    """FNV-1a over UTF-16 code units, matching the Web Reader implementation."""
    hash_value = 0x811C9DC5
    encoded = text.encode("utf-16-le", "surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = encoded[index] | (encoded[index + 1] << 8)
        hash_value ^= code_unit
        hash_value = (hash_value * 0x01000193) & 0xFFFFFFFF
    return f"{hash_value:08x}"


def build_multi_text_target_key(record_id: str, segments: list[dict[str, object]]) -> str:
    signature = "|".join(
        ":".join(
            [
                str(index),
                str(segment.get("paragraph_id") or ""),
                str(segment.get("sentence_id") or ""),
                str(segment.get("start_offset") or 0),
                str(segment.get("end_offset") or 0),
                str(segment.get("text_hash") or ""),
            ]
        )
        for index, segment in enumerate(segments)
    )
    return f"record:{record_id}:multi_text:{len(segments)}:{compute_text_range_hash(signature)}"
