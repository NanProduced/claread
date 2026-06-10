from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast
from typing import Literal

from app.schemas.common import TextSpan
from app.schemas.internal.analysis import PreparedSentence

# 标点变体归一化映射，与 sanitize_text / normalize.py 保持一致。
# 在 resolve_text_anchor 入口处对 anchor_text 做归一化，
# 使 Level 1-3 的匹配就能处理弯引号/dash/省略号差异。
_ANCHOR_PUNCTUATION_MAP = str.maketrans({
    "\u2018": "'",   # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",   # RIGHT SINGLE QUOTATION MARK
    "\u201c": '"',   # LEFT DOUBLE QUOTATION MARK
    "\u201d": '"',   # RIGHT DOUBLE QUOTATION MARK
    "\u2013": "-",   # EN DASH
    "\u2014": "-",   # EM DASH
})

# 省略号字符 → 三个点（str.maketrans 无法做 1→3 映射，需单独处理）
_ANCHOR_ELLIPSIS_PATTERN = re.compile(r"\u2026")

QUOTE_CLASS = r"[\"'""'']"
HYPHEN_CLASS = r"[-–—]"
SEPARATOR_CLASS = r"[\s–—-]"
GRAMMAR_ANCHOR_BOUNDARY_PUNCTUATION = " \t\r\n,.;:!?，。；：！？"
_PEDAGOGICAL_SLOT_PATTERN = re.compile(
    r"(?:\bsb\b\.?|\bsth\b\.?|\bsomebody\b|\bsomeone\b|\bsomething\b|\boneself\b|one's)",
    flags=re.IGNORECASE,
)
_PEDAGOGICAL_TO_DO_PATTERN = re.compile(r"\bto\s+do\s+\.\.\.", flags=re.IGNORECASE)
_WORD_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")


@dataclass(frozen=True)
class ResolvedAnchorText:
    text: str
    span: TextSpan
    resolution_kind: Literal[
        "exact",
        "canonicalized",
        "boundary_trimmed",
        "schematic_ellipsis_expanded",
        "ordered_token_expanded",
        "pedagogical_pattern_expanded",
    ]


@dataclass(frozen=True)
class ResolvedAnchorPart:
    text: str
    span: TextSpan
    occurrence: int | None = None


@dataclass(frozen=True)
class ResolvedVocabularyAnchor:
    kind: Literal["text", "multi_text"]
    resolution_kind: Literal[
        "exact",
        "canonicalized",
        "boundary_trimmed",
        "schematic_multi_text",
        "pedagogical_pattern_multi_text",
    ]
    text: str | None = None
    span: TextSpan | None = None
    parts: tuple[ResolvedAnchorPart, ...] = ()


def _find_all(text: str, needle: str) -> list[tuple[int, int]]:
    results: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return results
        results.append((index, index + len(needle)))
        start = index + len(needle)


def _normalize_anchor_input(anchor_text: str) -> str:
    anchor_text = anchor_text.translate(_ANCHOR_PUNCTUATION_MAP)
    return _ANCHOR_ELLIPSIS_PATTERN.sub("...", anchor_text)


def _normalize_pedagogical_anchor(anchor_text: str) -> str:
    normalized = _normalize_anchor_input(anchor_text)
    normalized = _PEDAGOGICAL_SLOT_PATTERN.sub("...", normalized)
    normalized = _PEDAGOGICAL_TO_DO_PATTERN.sub("to ...", normalized)
    normalized = re.sub(r"(?:\s*\.\.\.\s*){2,}", " ... ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _resolve_exact_or_flexible_matches(
    sentence: PreparedSentence,
    anchor_text: str,
) -> list[tuple[int, int]]:
    normalized_anchor = _normalize_anchor_input(anchor_text)

    exact_matches = _find_all(sentence.text, normalized_anchor)
    if exact_matches:
        return exact_matches

    casefold_matches = [
        (match.start(), match.end())
        for match in re.finditer(re.escape(normalized_anchor), sentence.text, flags=re.IGNORECASE)
    ]
    if casefold_matches:
        return casefold_matches

    flexible_matches = [
        (match.start(), match.end())
        for match in re.finditer(
            _build_flexible_pattern(normalized_anchor),
            sentence.text,
            flags=re.IGNORECASE,
        )
    ]
    if flexible_matches:
        return flexible_matches

    normalized_text, index_map = _normalize_for_matching(sentence.text)
    normalized_anchor_text, _ = _normalize_for_matching(normalized_anchor)
    if not normalized_anchor_text:
        return []

    normalized_matches = _find_all(normalized_text, normalized_anchor_text)
    if not normalized_matches:
        return []

    return [
        (index_map[start], index_map[end - 1] + 1)
        for start, end in normalized_matches
    ]


def _word_tokens(text: str) -> list[tuple[str, int, int]]:
    return [
        (match.group(0).casefold(), match.start(), match.end())
        for match in _WORD_TOKEN_PATTERN.finditer(text)
    ]


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


def source_substring_from_span(
    sentence: PreparedSentence,
    span: TextSpan,
) -> str | None:
    local_start = span.start - sentence.sentence_span.start
    local_end = span.end - sentence.sentence_span.start
    if local_start < 0 or local_end > len(sentence.text) or local_start >= local_end:
        return None
    return sentence.text[local_start:local_end]


def _source_occurrence_for_span(
    sentence: PreparedSentence,
    anchor_text: str,
    span: TextSpan,
) -> int | None:
    local_start = span.start - sentence.sentence_span.start
    matches = _find_all(sentence.text, anchor_text)
    if len(matches) <= 1:
        return None
    for index, (start, _end) in enumerate(matches, start=1):
        if start == local_start:
            return index
    return None


def canonicalize_text_anchor_to_source(
    sentence: PreparedSentence,
    anchor_text: str,
    anchor_occurrence: int | None = None,
) -> ResolvedAnchorText | None:
    span = resolve_text_anchor(sentence, anchor_text, anchor_occurrence)
    if span is None:
        return None
    canonical_text = source_substring_from_span(sentence, span)
    if canonical_text is None:
        return None
    resolution_kind: Literal["exact", "canonicalized"] = (
        "exact" if canonical_text == anchor_text else "canonicalized"
    )
    return ResolvedAnchorText(
        text=canonical_text,
        span=span,
        resolution_kind=resolution_kind,
    )


def _wrap_vocabulary_text_anchor(resolved: ResolvedAnchorText) -> ResolvedVocabularyAnchor:
    return ResolvedVocabularyAnchor(
        kind="text",
        text=resolved.text,
        span=resolved.span,
        resolution_kind=cast(
            Literal["exact", "canonicalized", "boundary_trimmed"],
            resolved.resolution_kind,
        ),
    )


def recover_schematic_ellipsis_anchor_text(
    sentence: PreparedSentence,
    anchor_text: str,
) -> ResolvedAnchorText | None:
    normalized_anchor = _normalize_anchor_input(anchor_text)
    if "..." not in normalized_anchor or normalized_anchor in sentence.text:
        return None

    segments = [
        segment.strip(GRAMMAR_ANCHOR_BOUNDARY_PUNCTUATION)
        for segment in normalized_anchor.split("...")
        if segment.strip(GRAMMAR_ANCHOR_BOUNDARY_PUNCTUATION)
    ]
    if len(segments) < 2:
        return None

    candidate_groups = [
        _resolve_exact_or_flexible_matches(sentence, segment)
        for segment in segments
    ]
    if any(not group for group in candidate_groups):
        return None

    chains: list[list[tuple[int, int]]] = []

    def _search(idx: int, prev_end: int, chosen: list[tuple[int, int]]) -> None:
        if len(chains) > 1:
            return
        if idx == len(candidate_groups):
            chains.append(chosen.copy())
            return
        for start, end in candidate_groups[idx]:
            if start < prev_end:
                continue
            chosen.append((start, end))
            _search(idx + 1, end, chosen)
            chosen.pop()
            if len(chains) > 1:
                return

    _search(0, 0, [])
    if len(chains) != 1:
        return None

    start, _ = chains[0][0]
    _, end = chains[0][-1]
    span = TextSpan(
        start=sentence.sentence_span.start + start,
        end=sentence.sentence_span.start + end,
    )
    canonical_text = source_substring_from_span(sentence, span)
    if canonical_text is None:
        return None
    return ResolvedAnchorText(
        text=canonical_text,
        span=span,
        resolution_kind="schematic_ellipsis_expanded",
    )


def recover_ordered_token_anchor_text(
    sentence: PreparedSentence,
    anchor_text: str,
    anchor_occurrence: int | None = None,
    *,
    max_extra_tokens: int = 2,
) -> ResolvedAnchorText | None:
    normalized_anchor = _normalize_anchor_input(anchor_text)
    anchor_tokens = [token for token, _start, _end in _word_tokens(normalized_anchor)]
    sentence_tokens = _word_tokens(sentence.text)
    if len(anchor_tokens) < 2 or len(sentence_tokens) < len(anchor_tokens):
        return None

    candidate_token_spans: list[tuple[int, int]] = []

    def _search(anchor_idx: int, sentence_idx: int, chosen: list[int]) -> None:
        if anchor_idx == len(anchor_tokens):
            start_idx = chosen[0]
            end_idx = chosen[-1]
            extra_tokens = (end_idx - start_idx + 1) - len(anchor_tokens)
            if 1 <= extra_tokens <= max_extra_tokens:
                candidate_token_spans.append((start_idx, end_idx))
            return

        token = anchor_tokens[anchor_idx]
        for current_idx in range(sentence_idx, len(sentence_tokens)):
            if sentence_tokens[current_idx][0] != token:
                continue
            chosen.append(current_idx)
            _search(anchor_idx + 1, current_idx + 1, chosen)
            chosen.pop()

    _search(0, 0, [])
    if not candidate_token_spans:
        return None

    unique_token_spans = sorted(set(candidate_token_spans))
    chosen_span = _resolve_candidate(unique_token_spans, anchor_occurrence)
    if chosen_span is None:
        return None

    start_token = sentence_tokens[chosen_span[0]]
    end_token = sentence_tokens[chosen_span[1]]
    span = TextSpan(
        start=sentence.sentence_span.start + start_token[1],
        end=sentence.sentence_span.start + end_token[2],
    )
    canonical_text = source_substring_from_span(sentence, span)
    if canonical_text is None:
        return None
    return ResolvedAnchorText(
        text=canonical_text,
        span=span,
        resolution_kind="ordered_token_expanded",
    )


def resolve_grammar_anchor_to_source(
    sentence: PreparedSentence,
    anchor_text: str,
    anchor_occurrence: int | None = None,
) -> ResolvedAnchorText | None:
    trimmed_anchor = anchor_text.strip(GRAMMAR_ANCHOR_BOUNDARY_PUNCTUATION)
    if trimmed_anchor and trimmed_anchor != anchor_text:
        trimmed = canonicalize_text_anchor_to_source(sentence, trimmed_anchor, anchor_occurrence)
        if trimmed is not None:
            return ResolvedAnchorText(
                text=trimmed.text,
                span=trimmed.span,
                resolution_kind="boundary_trimmed",
            )

    canonical = canonicalize_text_anchor_to_source(sentence, anchor_text, anchor_occurrence)
    if canonical is not None:
        return canonical

    return recover_schematic_ellipsis_anchor_text(sentence, anchor_text)


def _extract_vocabulary_schematic_parts(
    anchor_text: str,
) -> tuple[list[str], Literal["schematic_multi_text", "pedagogical_pattern_multi_text"]] | None:
    normalized = _normalize_anchor_input(anchor_text)
    if not normalized:
        return None

    working = normalized
    pedagogical = False

    if re.search(r"\bto\s+do\s+(?:sth|something)\b\.?", working, flags=re.IGNORECASE):
        pedagogical = True
        working = re.sub(
            r"\bto\s+do\s+(?:sth|something)\b\.?",
            " to ... ",
            working,
            flags=re.IGNORECASE,
        )

    slot_patterns = [
        r"\bsb\s*/\s*sth\b\.?",
        r"\bsth\s*/\s*sb\b\.?",
        r"\bsb\b\.?",
        r"\bsth\b\.?",
        r"\bsomebody\b",
        r"\bsomeone\b",
        r"\bsomething\b",
        r"\boneself\b",
        r"\bone's\b",
        r"\bdo\s+sth\b\.?",
        r"\bdo\s+something\b\.?",
    ]
    for pattern in slot_patterns:
        if re.search(pattern, working, flags=re.IGNORECASE):
            pedagogical = True
            working = re.sub(pattern, " ... ", working, flags=re.IGNORECASE)

    if "..." not in working:
        return None

    working = re.sub(r"(?:\s*\.\.\.\s*){2,}", " ... ", working)
    working = re.sub(r"\s+", " ", working).strip()
    parts = [
        segment.strip(GRAMMAR_ANCHOR_BOUNDARY_PUNCTUATION)
        for segment in working.split("...")
        if segment.strip(GRAMMAR_ANCHOR_BOUNDARY_PUNCTUATION)
    ]
    if len(parts) < 2:
        return None
    return (
        parts,
        "pedagogical_pattern_multi_text" if pedagogical else "schematic_multi_text",
    )


def _resolve_ordered_anchor_part_chain(
    sentence: PreparedSentence,
    segments: list[str],
    anchor_occurrence: int | None = None,
) -> tuple[ResolvedAnchorPart, ...] | None:
    candidate_groups = [
        _resolve_exact_or_flexible_matches(sentence, segment)
        for segment in segments
    ]
    if any(not group for group in candidate_groups):
        return None

    chains: list[tuple[tuple[int, int], ...]] = []

    def _search(idx: int, prev_end: int, chosen: list[tuple[int, int]]) -> None:
        if anchor_occurrence is None and len(chains) > 1:
            return
        if idx == len(candidate_groups):
            chains.append(tuple(chosen))
            return
        for start, end in candidate_groups[idx]:
            if start < prev_end:
                continue
            chosen.append((start, end))
            _search(idx + 1, end, chosen)
            chosen.pop()
            if anchor_occurrence is None and len(chains) > 1:
                return

    _search(0, 0, [])
    if not chains:
        return None

    unique_chains: list[tuple[tuple[int, int], ...]] = []
    seen_chains: set[tuple[tuple[int, int], ...]] = set()
    for chain in chains:
        if chain in seen_chains:
            continue
        seen_chains.add(chain)
        unique_chains.append(chain)

    if anchor_occurrence is None:
        if len(unique_chains) != 1:
            return None
        chosen_chain = unique_chains[0]
    else:
        if anchor_occurrence < 1 or anchor_occurrence > len(unique_chains):
            return None
        chosen_chain = unique_chains[anchor_occurrence - 1]

    resolved_parts: list[ResolvedAnchorPart] = []
    for start, end in chosen_chain:
        span = TextSpan(
            start=sentence.sentence_span.start + start,
            end=sentence.sentence_span.start + end,
        )
        text = source_substring_from_span(sentence, span)
        if text is None:
            return None
        resolved_parts.append(
            ResolvedAnchorPart(
                text=text,
                span=span,
                occurrence=_source_occurrence_for_span(sentence, text, span),
            )
        )
    return tuple(resolved_parts)


def resolve_vocabulary_anchor_binding(
    sentence: PreparedSentence,
    anchor_text: str,
    anchor_occurrence: int | None = None,
) -> ResolvedVocabularyAnchor | None:
    trimmed_anchor = anchor_text.strip(GRAMMAR_ANCHOR_BOUNDARY_PUNCTUATION)
    if trimmed_anchor and trimmed_anchor != anchor_text:
        trimmed = resolve_vocabulary_anchor_binding(sentence, trimmed_anchor, anchor_occurrence)
        if trimmed is not None:
            if trimmed.kind == "text":
                return ResolvedVocabularyAnchor(
                    kind="text",
                    text=trimmed.text,
                    span=trimmed.span,
                    resolution_kind="boundary_trimmed",
                )
            return trimmed

    canonical = canonicalize_text_anchor_to_source(sentence, anchor_text, anchor_occurrence)
    if canonical is not None:
        return _wrap_vocabulary_text_anchor(canonical)

    schematic = _extract_vocabulary_schematic_parts(anchor_text)
    if schematic is None:
        return None

    parts, resolution_kind = schematic
    resolved_parts = _resolve_ordered_anchor_part_chain(
        sentence,
        parts,
        anchor_occurrence,
    )
    if resolved_parts is None:
        return None

    return ResolvedVocabularyAnchor(
        kind="multi_text",
        resolution_kind=resolution_kind,
        parts=resolved_parts,
    )


def resolve_vocabulary_anchor_to_source(
    sentence: PreparedSentence,
    anchor_text: str,
    anchor_occurrence: int | None = None,
) -> ResolvedAnchorText | None:
    resolved = resolve_vocabulary_anchor_binding(sentence, anchor_text, anchor_occurrence)
    if resolved is None or resolved.kind != "text" or resolved.text is None or resolved.span is None:
        return None
    return ResolvedAnchorText(
        text=resolved.text,
        span=resolved.span,
        resolution_kind=cast(
            Literal["exact", "canonicalized", "boundary_trimmed"],
            resolved.resolution_kind,
        ),
    )


def resolve_vocabulary_anchor_spans(
    sentence: PreparedSentence,
    anchor_text: str,
    anchor_occurrence: int | None = None,
) -> tuple[TextSpan, ...] | None:
    resolved = resolve_vocabulary_anchor_binding(sentence, anchor_text, anchor_occurrence)
    if resolved is None:
        return None
    if resolved.kind == "text":
        if resolved.span is None:
            return None
        return (resolved.span,)
    return tuple(part.span for part in resolved.parts)


def resolve_text_anchor(
    sentence: PreparedSentence,
    anchor_text: str,
    anchor_occurrence: int | None = None,
) -> TextSpan | None:
    """仅在句内解析锚点，避免模型直接生成全文坐标。"""
    if not anchor_text.strip():
        return None

    # 对 anchor_text 做标点归一化，与 sanitize_text 保持一致。
    # sentence_text 已在预处理阶段归一化，但 LLM 输出的 anchor_text
    # 可能包含弯引号、en/em dash、省略号等标点变体。
    anchor_text = _normalize_anchor_input(anchor_text)

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


def resolve_explicit_anchor_parts(
    sentence: PreparedSentence,
    parts: list[dict[str, object]],
) -> tuple[ResolvedAnchorPart, ...] | None:
    resolved_parts: list[ResolvedAnchorPart] = []
    previous_end: int | None = None

    for part in parts:
        anchor_text = str(part.get("anchor_text", ""))
        occurrence_val = part.get("occurrence")
        occurrence: int | None = None if occurrence_val is None else cast(int, occurrence_val)

        span = resolve_text_anchor(sentence, anchor_text, occurrence)
        if span is None:
            return None
        if previous_end is not None and span.start < previous_end:
            return None

        text = source_substring_from_span(sentence, span)
        if text is None:
            return None

        resolved_parts.append(
            ResolvedAnchorPart(
                text=text,
                span=span,
                occurrence=_source_occurrence_for_span(sentence, text, span),
            )
        )
        previous_end = span.end

    return tuple(resolved_parts)


def resolve_multi_text_anchor(
    sentence: PreparedSentence,
    parts: list[dict[str, object]],
) -> list[TextSpan] | None:
    """
    解析多段锚点（用于 so...that, not only...but also 等不连续结构）。
    返回各部分的 TextSpan 列表，如果任一部分无法定位则返回 None。
    """
    resolved_parts = resolve_explicit_anchor_parts(sentence, parts)
    if resolved_parts is None:
        return None
    return [part.span for part in resolved_parts]
