from __future__ import annotations

import re
from typing import cast

from app.schemas.common import TextSpan
from app.schemas.internal.analysis import PreparedSentence

QUOTE_CLASS = r"[\"'""'']"
HYPHEN_CLASS = r"[-–—]"
SEPARATOR_CLASS = r"[\s–—-]"


def _find_all(text: str, needle: str) -> list[tuple[int, int]]:
    results: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return results
        results.append((index, index + len(needle)))
        start = index + len(needle)


def _build_flexible_pattern(anchor_text: str) -> str:
    parts: list[str] = []
    for char in anchor_text:
        if char.isspace():
            parts.append(r"\s+")
        elif char in "\"'""''":
            parts.append(QUOTE_CLASS)
        elif char in "-–—":
            parts.append(HYPHEN_CLASS)
        else:
            parts.append(re.escape(char))
    return "".join(parts)


def _normalize_for_matching(text: str) -> tuple[str, list[int]]:
    """把句子归一化为稳定匹配串，并保留归一化字符到原文索引的映射。"""
    normalized_chars: list[str] = []
    index_map: list[int] = []
    last_was_separator = False

    for index, char in enumerate(text):
        if char in "\"'""''":
            continue
        if re.fullmatch(SEPARATOR_CLASS, char):
            if normalized_chars and not last_was_separator:
                normalized_chars.append(" ")
                index_map.append(index)
            last_was_separator = True
            continue

        normalized_chars.append(char.casefold())
        index_map.append(index)
        last_was_separator = False

    if normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        index_map.pop()

    return "".join(normalized_chars), index_map


def _resolve_candidate(
    matches: list[tuple[int, int]],
    anchor_occurrence: int | None,
) -> tuple[int, int] | None:
    if not matches:
        return None
    if anchor_occurrence is not None:
        if 1 <= anchor_occurrence <= len(matches):
            return matches[anchor_occurrence - 1]
        return None
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_text_anchor(
    sentence: PreparedSentence,
    anchor_text: str,
    anchor_occurrence: int | None = None,
) -> TextSpan | None:
    """仅在句内解析锚点，避免模型直接生成全文坐标。"""
    if not anchor_text.strip():
        return None

    exact = _resolve_candidate(_find_all(sentence.text, anchor_text), anchor_occurrence)
    if exact is not None:
        return TextSpan(
            start=sentence.sentence_span.start + exact[0],
            end=sentence.sentence_span.start + exact[1],
        )

    casefold_matches = [
        (match.start(), match.end())
        for match in re.finditer(re.escape(anchor_text), sentence.text, flags=re.IGNORECASE)
    ]
    casefold = _resolve_candidate(casefold_matches, anchor_occurrence)
    if casefold is not None:
        return TextSpan(
            start=sentence.sentence_span.start + casefold[0],
            end=sentence.sentence_span.start + casefold[1],
        )

    flexible_matches = [
        (match.start(), match.end())
        for match in re.finditer(
            _build_flexible_pattern(anchor_text),
            sentence.text,
            flags=re.IGNORECASE,
        )
    ]
    flexible = _resolve_candidate(flexible_matches, anchor_occurrence)
    if flexible is not None:
        return TextSpan(
            start=sentence.sentence_span.start + flexible[0],
            end=sentence.sentence_span.start + flexible[1],
        )

    normalized_text, index_map = _normalize_for_matching(sentence.text)
    normalized_anchor, _ = _normalize_for_matching(anchor_text)
    if not normalized_anchor:
        return None

    normalized_matches = _find_all(normalized_text, normalized_anchor)
    normalized = _resolve_candidate(normalized_matches, anchor_occurrence)
    if normalized is None:
        return None

    start_index = index_map[normalized[0]]
    end_index = index_map[normalized[1] - 1] + 1
    return TextSpan(
        start=sentence.sentence_span.start + start_index,
        end=sentence.sentence_span.start + end_index,
    )


def resolve_multi_text_anchor(
    sentence: PreparedSentence,
    parts: list[dict[str, object]],
) -> list[TextSpan] | None:
    """
    解析多段锚点（用于 so...that, not only...but also 等不连续结构）。
    返回各部分的 TextSpan 列表，如果任一部分无法定位则返回 None。
    """
    resolved_parts: list[TextSpan] = []

    for part in parts:
        anchor_text = str(part.get("anchor_text", ""))
        occurrence_val = part.get("occurrence")
        occurrence: int | None = None if occurrence_val is None else cast(int, occurrence_val)

        span = resolve_text_anchor(sentence, anchor_text, occurrence)
        if span is None:
            return None
        resolved_parts.append(span)

    return resolved_parts
