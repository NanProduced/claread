from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)

LOW_IMPACT_CANONICALIZER_VERSION = "reader_base_low_impact_v1"
DETERMINISTIC_READING_BASE_BUILDER_VERSION = "reading_base_builder_d3_p2_v1"
DETERMINISTIC_SEGMENTER_VERSION = "regex_sentence_clause_window_v1"
FALLBACK_WINDOW_WORD_COUNT = 24
# Canonicalizer version label used when the caller supplies an EXACT
# canonical text (already canonicalized by the stable document freeze
# plan) and the base builder must NOT recanonicalize it. D6 block
# offsets are bound to the exact canonical text.
EXACT_CANONICAL_TEXT_VERSION = "exact_canonical_text_v1"

_INVISIBLE_CHAR_PATTERN = re.compile(
    r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f"
    r"\u200b\u200c\u200d\u00ad\ufeff"
    r"\u200e\u200f\u202a-\u202e"
    r"\u2060-\u2064\u2066-\u2069"
    r"\ufffc]"
)
_UNICODE_SPACE_MAP = str.maketrans({
    "\u00a0": " ",
    "\u2000": " ",
    "\u2001": " ",
    "\u2002": " ",
    "\u2003": " ",
    "\u2004": " ",
    "\u2005": " ",
    "\u2006": " ",
    "\u2007": " ",
    "\u2008": " ",
    "\u2009": " ",
    "\u200a": " ",
    "\u202f": " ",
    "\u205f": " ",
    "\u3000": " ",
})
_BLANK_LINE_RUN_PATTERN = re.compile(r"\n(?:[ \t]*\n){2,}")
_WORD_PATTERN = re.compile(r"\S+")
_LIST_LINE_PATTERN = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")
_CLAUSE_BOUNDARY_PATTERN = re.compile(
    r";|:|—|–|--|,\s+(?=(?:and|but|or|so|yet|for|because|which|that|while|although|however)\b)",
    re.IGNORECASE,
)
_ABBREVIATION_SUFFIXES = {
    "u.s.",
    "u.k.",
    "ph.d.",
    "e.g.",
    "i.e.",
    "dr.",
    "mr.",
    "mrs.",
    "ms.",
    "prof.",
    "sr.",
    "jr.",
    "dept.",
    "govt.",
    "est.",
    "inc.",
    "ltd.",
    "corp.",
    "vs.",
    "approx.",
    "etc.",
}
_CLOSING_PUNCTUATION = "\"'”’)]}"
_SENTENCE_STARTERS = "\"'“‘([{"


@dataclass(frozen=True, slots=True)
class LowImpactReadingBaseBuildInput:
    reading_record_id: str
    base_id: str
    source_text: str
    title: str | None = None
    language: str | None = None
    canonicalizer_version: str = LOW_IMPACT_CANONICALIZER_VERSION
    builder_version: str = DETERMINISTIC_READING_BASE_BUILDER_VERSION
    segmenter_version: str = DETERMINISTIC_SEGMENTER_VERSION


@dataclass(frozen=True, slots=True)
class StableReadingBase:
    reading_record_id: str
    base_id: str
    text: str
    content_sha256: str
    content_utf16_length: int
    canonicalizer_version: str
    builder_version: str
    segmenter_version: str
    language: str | None = None
    title_snapshot: str | None = None


@dataclass(frozen=True, slots=True)
class BuiltReadingUnit:
    reading_record_id: str
    base_id: str
    unit_id: str
    order_index: int
    unit_type: str
    boundary_quality: str
    base_start_utf16: int
    base_end_utf16: int
    text_hash: str
    text: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class BuiltAnchorSegment:
    reading_record_id: str
    base_id: str
    unit_id: str
    anchor_segment_id: str
    sentence_id: str
    paragraph_id: str
    order_index: int
    unit_order_index: int
    segment_type: str
    boundary_quality: str
    base_start_utf16: int
    base_end_utf16: int
    unit_start_utf16: int
    unit_end_utf16: int
    text_hash: str
    text: str


@dataclass(frozen=True, slots=True)
class NavigationUnitFact:
    unit_id: str
    order_index: int
    unit_type: str
    boundary_quality: str
    label: str | None
    base_start_utf16: int
    base_end_utf16: int


@dataclass(frozen=True, slots=True)
class ReadingBaseBuildResult:
    base: StableReadingBase
    units: tuple[BuiltReadingUnit, ...]
    anchor_segments: tuple[BuiltAnchorSegment, ...]
    navigation_units: tuple[NavigationUnitFact, ...]


@dataclass(frozen=True, slots=True)
class _SegmentSpan:
    start_char: int
    end_char: int
    segment_type: str
    boundary_quality: str


def canonicalize_low_impact_text(source_text: str) -> str:
    text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE_CHAR_PATTERN.sub("", text)
    text = text.translate(_UNICODE_SPACE_MAP)
    text = _BLANK_LINE_RUN_PATTERN.sub("\n\n", text)
    return text.strip()


def build_low_impact_reading_base(
    build_input: LowImpactReadingBaseBuildInput,
) -> ReadingBaseBuildResult:
    text = canonicalize_low_impact_text(build_input.source_text)
    if not text:
        raise ValueError("canonical low-impact text must not be empty")
    return _build_reading_base_core(
        reading_record_id=build_input.reading_record_id,
        base_id=build_input.base_id,
        text=text,
        title=build_input.title,
        language=build_input.language,
        canonicalizer_version=build_input.canonicalizer_version,
        builder_version=build_input.builder_version,
        segmenter_version=build_input.segmenter_version,
    )


def build_reading_base_from_canonical_text(
    *,
    reading_record_id: str,
    base_id: str,
    canonical_text: str,
    title: str | None = None,
    language: str | None = None,
    builder_version: str = DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    segmenter_version: str = DETERMINISTIC_SEGMENTER_VERSION,
    canonicalizer_version: str = EXACT_CANONICAL_TEXT_VERSION,
) -> ReadingBaseBuildResult:
    """Build a reading base from an EXACT canonical text.

    Unlike :func:`build_low_impact_reading_base`, this does NOT
    recanonicalize the text. The ``canonical_text`` is used as-is for
    unit/anchor segmentation and for ``content_sha256`` /
    ``content_utf16_length`` computation. This is required for D6-I2C
    where the stable document's block offsets are already bound to the
    exact canonical text produced by the freeze plan; recanonicalizing
    would invalidate those offsets.

    The private split/segment/hash helpers are reused so segmentation
    behavior is identical to the low-impact builder.

    Args:
        reading_record_id: The reading record id.
        base_id: The base id (UUID string).
        canonical_text: The EXACT canonical text from the stable
            document freeze plan. Must not be empty.
        title: Optional title snapshot.
        language: Optional language code.
        builder_version: Builder version label.
        segmenter_version: Segmenter version label.
        canonicalizer_version: Canonicalizer version label; defaults
            to :data:`EXACT_CANONICAL_TEXT_VERSION` to mark that the
            text was supplied exactly (not recanonicalized).

    Returns:
        A validated :class:`ReadingBaseBuildResult`.

    Raises:
        ValueError: If ``canonical_text`` is empty or the
            segmentation/validation fails.
    """
    if not canonical_text:
        raise ValueError("canonical_text must not be empty")
    return _build_reading_base_core(
        reading_record_id=reading_record_id,
        base_id=base_id,
        text=canonical_text,
        title=title,
        language=language,
        canonicalizer_version=canonicalizer_version,
        builder_version=builder_version,
        segmenter_version=segmenter_version,
    )


def _build_reading_base_core(
    *,
    reading_record_id: str,
    base_id: str,
    text: str,
    title: str | None,
    language: str | None,
    canonicalizer_version: str,
    builder_version: str,
    segmenter_version: str,
) -> ReadingBaseBuildResult:
    """Core builder shared by :func:`build_low_impact_reading_base` and
    :func:`build_reading_base_from_canonical_text`.

    The caller is responsible for ensuring ``text`` is the exact
    canonical text (already canonicalized if needed). This helper does
    NOT recanonicalize; it only segments the supplied text into units
    and anchor segments and validates the result.
    """
    utf16_prefix = _build_utf16_prefix(text)
    block_spans = _split_structure_blocks(text)
    if not block_spans:
        raise ValueError("non-empty canonical text must produce at least one structure block")

    base = StableReadingBase(
        reading_record_id=reading_record_id,
        base_id=base_id,
        text=text,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        content_utf16_length=utf16_prefix[-1],
        canonicalizer_version=canonicalizer_version,
        builder_version=builder_version,
        segmenter_version=segmenter_version,
        language=language,
        title_snapshot=title,
    )

    units: list[BuiltReadingUnit] = []
    anchor_segments: list[BuiltAnchorSegment] = []
    navigation_units: list[NavigationUnitFact] = []

    for block_index, (char_start, char_end) in enumerate(block_spans, start=1):
        unit_id = f"u{len(units) + 1}"
        paragraph_id = f"p{block_index}"
        block_text = text[char_start:char_end]
        segment_spans = _build_segment_spans(block_text)
        if not segment_spans:
            raise ValueError(f"structure block {paragraph_id} did not produce anchor segments")

        base_start_utf16 = utf16_prefix[char_start]
        base_end_utf16 = utf16_prefix[char_end]
        unit_type = _classify_unit_type(block_text)
        label = _build_unit_label(block_text, unit_type)

        built_segments: list[BuiltAnchorSegment] = []
        unit_boundary_quality = "normal"
        for unit_order_index, span in enumerate(segment_spans, start=1):
            anchor_segment_id = f"s{len(anchor_segments) + 1}"
            segment_text = block_text[span.start_char:span.end_char]
            segment_base_start_utf16 = utf16_prefix[char_start + span.start_char]
            segment_base_end_utf16 = utf16_prefix[char_start + span.end_char]
            built_segment = BuiltAnchorSegment(
                reading_record_id=reading_record_id,
                base_id=base_id,
                unit_id=unit_id,
                anchor_segment_id=anchor_segment_id,
                sentence_id=anchor_segment_id,
                paragraph_id=paragraph_id,
                order_index=len(anchor_segments) + 1,
                unit_order_index=unit_order_index,
                segment_type=span.segment_type,
                boundary_quality=span.boundary_quality,
                base_start_utf16=segment_base_start_utf16,
                base_end_utf16=segment_base_end_utf16,
                unit_start_utf16=segment_base_start_utf16 - base_start_utf16,
                unit_end_utf16=segment_base_end_utf16 - base_start_utf16,
                text_hash=compute_text_range_hash(segment_text),
                text=segment_text,
            )
            built_segments.append(built_segment)
            anchor_segments.append(built_segment)
            if span.boundary_quality == "low":
                unit_boundary_quality = "low"

        if unit_type == "body" and all(
            segment.segment_type == "fallback_window" for segment in built_segments
        ):
            unit_type = "fallback"

        unit_text_hash = compute_text_range_hash(block_text)
        built_unit = BuiltReadingUnit(
            reading_record_id=reading_record_id,
            base_id=base_id,
            unit_id=unit_id,
            order_index=len(units) + 1,
            unit_type=unit_type,
            boundary_quality=unit_boundary_quality,
            base_start_utf16=base_start_utf16,
            base_end_utf16=base_end_utf16,
            text_hash=unit_text_hash,
            text=block_text,
            label=label,
        )
        units.append(built_unit)
        navigation_units.append(
            NavigationUnitFact(
                unit_id=unit_id,
                order_index=built_unit.order_index,
                unit_type=unit_type,
                boundary_quality=unit_boundary_quality,
                label=label,
                base_start_utf16=base_start_utf16,
                base_end_utf16=base_end_utf16,
            )
        )

    result = ReadingBaseBuildResult(
        base=base,
        units=tuple(units),
        anchor_segments=tuple(anchor_segments),
        navigation_units=tuple(navigation_units),
    )
    validate_reading_base_build_result(result)
    return result


def _build_utf16_prefix(text: str) -> list[int]:
    prefix = [0]
    for char in text:
        prefix.append(prefix[-1] + (2 if ord(char) > 0xFFFF else 1))
    return prefix


def _split_structure_blocks(text: str) -> list[tuple[int, int]]:
    block_spans: list[tuple[int, int]] = []
    block_start: int | None = None
    block_end: int | None = None
    line_start = 0

    for line in text.split("\n"):
        line_end = line_start + len(line)
        stripped = line.strip()
        if stripped:
            first_visible = len(line) - len(line.lstrip())
            last_visible = len(line.rstrip())
            visible_start = line_start + first_visible
            visible_end = line_start + last_visible
            if block_start is None:
                block_start = visible_start
            block_end = visible_end
        elif block_start is not None and block_end is not None:
            block_spans.append((block_start, block_end))
            block_start = None
            block_end = None
        line_start = line_end + 1

    if block_start is not None and block_end is not None:
        block_spans.append((block_start, block_end))

    return block_spans


def _build_segment_spans(block_text: str) -> list[_SegmentSpan]:
    sentence_spans, sentence_boundary_count = _segment_sentence_spans(block_text)
    if (
        sentence_spans
        and sentence_boundary_count > 0
        and _spans_cover_visible_text(block_text, sentence_spans)
    ):
        return [
            _SegmentSpan(
                start_char=start,
                end_char=end,
                segment_type="sentence",
                boundary_quality=_sentence_boundary_quality(block_text[start:end]),
            )
            for start, end in sentence_spans
        ]

    clause_spans = _segment_clause_spans(block_text)
    if clause_spans and _spans_cover_visible_text(block_text, clause_spans):
        return [
            _SegmentSpan(
                start_char=start,
                end_char=end,
                segment_type="clause",
                boundary_quality="normal",
            )
            for start, end in clause_spans
        ]

    fallback_spans = _segment_fallback_windows(block_text)
    return [
        _SegmentSpan(
            start_char=start,
            end_char=end,
            segment_type="fallback_window",
            boundary_quality="low",
        )
        for start, end in fallback_spans
    ]


def _segment_sentence_spans(block_text: str) -> tuple[list[tuple[int, int]], int]:
    spans: list[tuple[int, int]] = []
    start = _next_visible_index(block_text, 0)
    if start >= len(block_text):
        return [], 0

    boundary_count = 0
    index = start
    while index < len(block_text):
        char = block_text[index]
        if char in ".!?":
            if char == "." and index + 1 < len(block_text) and block_text[index + 1] == ".":
                index += 1
                continue

            boundary_end = index + 1
            while (
                boundary_end < len(block_text)
                and block_text[boundary_end] in _CLOSING_PUNCTUATION
            ):
                boundary_end += 1

            if _is_sentence_boundary(block_text, start, boundary_end):
                trimmed_end = _trim_trailing_whitespace(block_text, boundary_end)
                if trimmed_end > start:
                    spans.append((start, trimmed_end))
                    boundary_count += 1
                start = _next_visible_index(block_text, boundary_end)
                index = start
                continue
        index += 1

    final_end = _trim_trailing_whitespace(block_text, len(block_text))
    if start < final_end:
        spans.append((start, final_end))
    return spans, boundary_count


def _is_sentence_boundary(block_text: str, start: int, boundary_end: int) -> bool:
    if boundary_end >= len(block_text):
        return True

    visible_next = _next_visible_index(block_text, boundary_end)
    if visible_next >= len(block_text):
        return True

    # Locate the sentence-ending punctuation (. ! ?) that triggered this
    # check. ``boundary_end`` is advanced past the punctuation and any
    # trailing closing punctuation (quotes, brackets), so scan backwards
    # through closing punctuation to find the actual sentence terminator.
    punct_index = boundary_end - 1
    while punct_index >= start and block_text[punct_index] in _CLOSING_PUNCTUATION:
        punct_index -= 1

    # Decimal number guard: "digit . digit" is NOT a sentence boundary.
    # This prevents splitting "$2.13 per hour" at "$2." and "3.5 million"
    # at "3.". The dot is a decimal separator, not a sentence terminator.
    if (
        punct_index > start
        and block_text[punct_index] == "."
        and block_text[punct_index - 1].isdigit()
        and block_text[visible_next].isdigit()
    ):
        return False

    tail = block_text[max(start, boundary_end - 20):boundary_end].lower()
    if block_text[boundary_end - 1] == "." and any(
        tail.endswith(abbreviation) for abbreviation in _ABBREVIATION_SUFFIXES
    ):
        return False

    next_char = block_text[visible_next]
    return (
        next_char.isupper()
        or next_char.isdigit()
        or next_char in _SENTENCE_STARTERS
        or "\u4e00" <= next_char <= "\u9fff"
    )


def _segment_clause_spans(block_text: str) -> list[tuple[int, int]]:
    matches = list(_CLAUSE_BOUNDARY_PATTERN.finditer(block_text))
    if not matches:
        if _word_count(block_text) <= 20:
            start = _next_visible_index(block_text, 0)
            end = _trim_trailing_whitespace(block_text, len(block_text))
            return [(start, end)] if start < end else []
        return []

    spans: list[tuple[int, int]] = []
    start = _next_visible_index(block_text, 0)
    for match in matches:
        match_text = match.group(0)
        split_end = match.start() + 1 if match_text.startswith(",") else match.end()
        end = _trim_trailing_whitespace(block_text, split_end)
        if end > start:
            spans.append((start, end))
        start = _next_visible_index(block_text, split_end)

    final_end = _trim_trailing_whitespace(block_text, len(block_text))
    if start < final_end:
        spans.append((start, final_end))
    return spans


def _segment_fallback_windows(block_text: str) -> list[tuple[int, int]]:
    tokens = list(_WORD_PATTERN.finditer(block_text))
    if not tokens:
        return []

    spans: list[tuple[int, int]] = []
    for start_index in range(0, len(tokens), FALLBACK_WINDOW_WORD_COUNT):
        window = tokens[start_index:start_index + FALLBACK_WINDOW_WORD_COUNT]
        spans.append((window[0].start(), window[-1].end()))
    return spans


def _sentence_boundary_quality(segment_text: str) -> str:
    if len(segment_text) > 280:
        return "low"
    return "normal"


def _spans_cover_visible_text(text: str, spans: list[tuple[int, int]]) -> bool:
    if not spans:
        return False

    first_visible = _next_visible_index(text, 0)
    if first_visible >= len(text):
        return False
    if text[first_visible:spans[0][0]].strip():
        return False

    previous_end = spans[0][1]
    if spans[0][0] >= previous_end:
        return False
    for start, end in spans[1:]:
        if start >= end or start < previous_end:
            return False
        if text[previous_end:start].strip():
            return False
        previous_end = end

    final_end = _trim_trailing_whitespace(text, len(text))
    return not text[previous_end:final_end].strip()


def _classify_unit_type(block_text: str) -> str:
    lines = [line.strip() for line in block_text.splitlines() if line.strip()]
    if not lines:
        return "unknown"
    if all(_LIST_LINE_PATTERN.match(line) for line in lines):
        return "list"
    if all(line.startswith(">") for line in lines):
        return "quote"
    if len(lines) == 1:
        line = lines[0]
        if len(line) <= 80 and len(line.split()) <= 12 and not re.search(r"[.!?;:]", line):
            return "heading"
    return "body"


def _build_unit_label(block_text: str, unit_type: str) -> str | None:
    if unit_type != "heading":
        return None
    return " ".join(block_text.split())


def _next_visible_index(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _trim_trailing_whitespace(text: str, end: int) -> int:
    index = end
    while index > 0 and text[index - 1].isspace():
        index -= 1
    return index


def _word_count(text: str) -> int:
    return len(_WORD_PATTERN.findall(text))


def validate_reading_base_build_result(result: ReadingBaseBuildResult) -> None:
    base_text = result.base.text
    units = list(result.units)
    segments = list(result.anchor_segments)

    if not units or not segments:
        raise ValueError("non-empty stable base must produce units and anchor segments")

    _validate_absolute_spans(base_text, units)
    _validate_unit_anchors(base_text, units, segments)


def _validate_absolute_spans(base_text: str, units: list[BuiltReadingUnit]) -> None:
    previous_end: int | None = None
    for unit in units:
        sliced = slice_by_utf16_offsets(base_text, unit.base_start_utf16, unit.base_end_utf16)
        if sliced != unit.text:
            raise ValueError(f"unit {unit.unit_id} does not round-trip to stable base text")
        if compute_text_range_hash(unit.text) != unit.text_hash:
            raise ValueError(f"unit {unit.unit_id} text hash mismatch")
        if previous_end is None:
            leading = slice_by_utf16_offsets(base_text, 0, unit.base_start_utf16)
            if leading and leading.strip():
                raise ValueError("leading gap before first unit must be whitespace only")
        else:
            if unit.base_start_utf16 < previous_end:
                raise ValueError("unit spans must not overlap")
            gap = slice_by_utf16_offsets(base_text, previous_end, unit.base_start_utf16)
            if gap and gap.strip():
                raise ValueError("unit gaps must be whitespace only")
        previous_end = unit.base_end_utf16

    if previous_end is None:
        return

    trailing = slice_by_utf16_offsets(base_text, previous_end, result_length_utf16(base_text))
    if trailing and trailing.strip():
        raise ValueError("trailing gap after last unit must be whitespace only")


def _validate_unit_anchors(
    base_text: str,
    units: list[BuiltReadingUnit],
    segments: list[BuiltAnchorSegment],
) -> None:
    units_by_id = {unit.unit_id: unit for unit in units}
    grouped: dict[str, list[BuiltAnchorSegment]] = {unit.unit_id: [] for unit in units}

    for segment in segments:
        unit = units_by_id.get(segment.unit_id)
        if unit is None:
            raise ValueError(f"anchor segment {segment.anchor_segment_id} references unknown unit")
        if (
            segment.base_start_utf16 < unit.base_start_utf16
            or segment.base_end_utf16 > unit.base_end_utf16
        ):
            raise ValueError(f"anchor segment {segment.anchor_segment_id} is outside its unit")
        absolute_text = slice_by_utf16_offsets(
            base_text,
            segment.base_start_utf16,
            segment.base_end_utf16,
        )
        local_text = slice_by_utf16_offsets(
            unit.text,
            segment.unit_start_utf16,
            segment.unit_end_utf16,
        )
        if absolute_text != segment.text or local_text != segment.text:
            raise ValueError(f"anchor segment {segment.anchor_segment_id} does not round-trip")
        if compute_text_range_hash(segment.text) != segment.text_hash:
            raise ValueError(f"anchor segment {segment.anchor_segment_id} text hash mismatch")
        if segment.segment_type == "fallback_window" and segment.boundary_quality != "low":
            raise ValueError("fallback_window segments must be marked low quality")
        grouped[segment.unit_id].append(segment)

    for unit in units:
        unit_segments = grouped[unit.unit_id]
        if not unit_segments:
            raise ValueError(f"unit {unit.unit_id} must have at least one anchor segment")
        unit_segments.sort(key=lambda segment: segment.unit_order_index)

        previous_end: int | None = None
        for segment in unit_segments:
            if previous_end is None:
                leading = slice_by_utf16_offsets(unit.text, 0, segment.unit_start_utf16)
                if leading and leading.strip():
                    raise ValueError(f"unit {unit.unit_id} has a non-whitespace leading anchor gap")
            else:
                if segment.unit_start_utf16 < previous_end:
                    raise ValueError(f"unit {unit.unit_id} anchor segments must not overlap")
                gap = slice_by_utf16_offsets(unit.text, previous_end, segment.unit_start_utf16)
                if gap and gap.strip():
                    raise ValueError(f"unit {unit.unit_id} anchor gaps must be whitespace only")
            previous_end = segment.unit_end_utf16

        if previous_end is None:
            continue
        trailing = slice_by_utf16_offsets(unit.text, previous_end, result_length_utf16(unit.text))
        if trailing and trailing.strip():
            raise ValueError(f"unit {unit.unit_id} has a non-whitespace trailing anchor gap")


def result_length_utf16(text: str) -> int:
    return utf16_code_unit_length(text)
