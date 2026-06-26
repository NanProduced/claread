from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.schemas.reader_input_adapter import (
    InputAdapterSourceType,
    InputSuitabilityRequest,
    InputSuitabilityResult,
    InputSuitabilityOutcome,
    SourceLossFlag,
)

_MIN_ENGLISH_WORDS = 50
_MAX_WORDS_BEFORE_ENVELOPE = 8000
_MIN_ENGLISH_WORD_RATIO = 0.70
_HIGH_EXTRACTION_CONFIDENCE = 0.95
_LOW_OCR_CONFIDENCE = 0.85
_LOW_LAYOUT_CONFIDENCE = 0.90
_MAX_PREVIEW_LENGTH = 280

_ENGLISH_WORD_PATTERN = re.compile(r"\b[a-zA-Z]+(?:'[a-zA-Z]+)?\b")
_WORDLIKE_TOKEN_PATTERN = re.compile(
    r"[A-Za-z]+(?:'[A-Za-z]+)?|[\u4e00-\u9fff]+|\d+"
)
_SENTENCE_END_PATTERN = re.compile(r"[.!?](?:\s|$)")
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_LINK_ONLY_LINE_PATTERN = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*(?:https?://\S+|www\.\S+|\[[^\]]+\]\([^)]+\))\s*$",
    re.IGNORECASE,
)
_CODE_LINE_PATTERN = re.compile(
    r"^\s*(?:"
    r"#include\b|import\b|from\b.+\bimport\b|def\b|class\b|const\b|let\b|var\b|"
    r"function\b|if\s*\(|for\s*\(|while\s*\(|return\b|try\b|except\b|"
    r"SELECT\b|UPDATE\b|INSERT\b|DELETE\b|CREATE\b|ALTER\b|WITH\b|"
    r"<\?php|public\b|private\b|protected\b"
    r")",
    re.IGNORECASE,
)
_CODE_FENCE_PATTERN = re.compile(r"^\s*```|^\s*~~~", re.MULTILINE)
_CODE_FENCE_LINE_PATTERN = re.compile(r"^\s*([`~]{3,})([^\n]*)$")
_HTML_PATTERN = re.compile(r"<[A-Za-z][^>]*>")
_INLINE_MATH_PATTERN = re.compile(r"(?<!\$)\$[^$\n]+\$(?!\$)")
_BLOCK_MATH_PATTERN = re.compile(r"\$\$[\s\S]+?\$\$|\\\(|\\\[")
_MARKDOWN_TABLE_SEPARATOR_PATTERN = re.compile(
    r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*$",
    re.MULTILINE,
)
_MARKDOWN_TABLE_ROW_PATTERN = re.compile(r"^\s*\|.+\|\s*$", re.MULTILINE)
_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MARKDOWN_FOOTNOTE_PATTERN = re.compile(r"\[\^[^\]]+\]|^\[\^[^\]]+\]:", re.MULTILINE)
_MARKDOWN_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
_MARKDOWN_LIST_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S", re.MULTILINE)
_MARKDOWN_BLOCKQUOTE_PATTERN = re.compile(r"^\s*>\s+\S", re.MULTILINE)
_SUSPICIOUS_OCR_CHAR_PATTERN = re.compile(r"[�¦§¤]|[|]{2,}|[_]{3,}|[\\/]{3,}")
_HYPHENATED_LINE_BREAK_PATTERN = re.compile(r"[A-Za-z]-\n[A-Za-z]")


@dataclass(frozen=True, slots=True)
class _TextMetrics:
    word_count: int
    english_word_count: int
    english_word_ratio: float
    sentence_count: int
    nonempty_line_count: int
    link_only_line_ratio: float
    code_line_ratio: float
    short_line_ratio: float
    suspicious_ocr_char_ratio: float


@dataclass(frozen=True, slots=True)
class _MarkdownComplexity:
    has_complex_structure: bool
    has_table: bool
    has_image: bool
    has_footnote: bool
    has_raw_html: bool
    has_math: bool
    has_unclosed_fence: bool
    has_simple_markdown: bool


@dataclass(frozen=True, slots=True)
class _OcrSignals:
    low_confidence: bool
    layout_uncertain: bool
    noisy_text: bool


class InputSuitabilityGate:
    """Pure deterministic suitability gate for input-adapter routing."""

    def evaluate(
        self,
        request: InputSuitabilityRequest,
    ) -> InputSuitabilityResult:
        normalized_text = _normalize_text(request.text)
        preview = _build_preview(normalized_text)

        flags: list[SourceLossFlag] = []
        reasons: list[str] = []

        if not normalized_text:
            _add_flag(flags, "too_short_for_learning")
            reasons.append("Input is blank after normalization.")
            return _build_result(
                outcome="input_rejected_or_action_required",
                request=request,
                word_count=0,
                english_word_ratio=0.0,
                natural_language_score=0.0,
                flags=flags,
                reasons=reasons,
                preview=preview,
            )

        metrics = _measure_text(normalized_text)
        markdown = _detect_markdown_complexity(
            normalized_text,
            source_type=request.source_type,
            filename=request.filename,
        )
        ocr = _detect_ocr_signals(
            normalized_text,
            source_type=request.source_type,
            source_metadata=request.source_metadata,
            metrics=metrics,
        )

        reject_reasons: list[str] = []
        candidate_reasons: list[str] = []

        if metrics.english_word_count < _MIN_ENGLISH_WORDS:
            _add_flag(flags, "too_short_for_learning")
            reject_reasons.append(
                f"English content is too short for learning ({metrics.english_word_count} words)."
            )

        if metrics.word_count > _MAX_WORDS_BEFORE_ENVELOPE:
            _add_flag(flags, "too_long_requires_envelope")
            candidate_reasons.append(
                f"Input is too long to process as a single low-impact stable document ({metrics.word_count} words)."
            )

        if metrics.english_word_ratio < _MIN_ENGLISH_WORD_RATIO:
            _add_flag(flags, "non_english_or_mixed_language")
            reject_reasons.append(
                f"English word ratio is too low ({metrics.english_word_ratio:.2f})."
            )

        if _is_link_list_dominant(normalized_text, metrics):
            _add_flag(flags, "link_list_dominant")
            reject_reasons.append("Input is dominated by links or URL-only lines.")

        if _is_code_dominant(normalized_text, metrics):
            _add_flag(flags, "code_dominant")
            reject_reasons.append("Input is dominated by code-like structure.")

        if markdown.has_table:
            _add_flag(flags, "markdown_complex_structure")
            _add_flag(flags, "table_structure_uncertain")
            candidate_reasons.append(
                "Markdown table structure must be preserved instead of silently flattened."
            )
        if markdown.has_image:
            _add_flag(flags, "markdown_complex_structure")
            _add_flag(flags, "image_ocr_uncertain")
            candidate_reasons.append(
                "Markdown image blocks require candidate review so media truth is not lost."
            )
        if markdown.has_footnote:
            _add_flag(flags, "markdown_complex_structure")
            _add_flag(flags, "footnote_or_caption_merged")
            candidate_reasons.append(
                "Markdown footnotes require candidate review so note structure is preserved."
            )
        if markdown.has_raw_html:
            _add_flag(flags, "markdown_complex_structure")
            _add_flag(flags, "document_block_degraded")
            candidate_reasons.append(
                "Raw HTML requires candidate review instead of deterministic downgrade."
            )
        if markdown.has_math:
            _add_flag(flags, "markdown_complex_structure")
            _add_flag(flags, "document_block_degraded")
            candidate_reasons.append(
                "Math syntax requires candidate review instead of deterministic downgrade."
            )
        if markdown.has_unclosed_fence:
            _add_flag(flags, "markdown_complex_structure")
            _add_flag(flags, "document_block_degraded")
            candidate_reasons.append(
                "Unclosed fenced code would degrade author-visible block boundaries during deterministic normalization."
            )

        if ocr.low_confidence:
            _add_flag(flags, "ocr_low_confidence")
            candidate_reasons.append(
                "OCR confidence is too low for direct stable-document freeze."
            )
        if ocr.layout_uncertain:
            _add_flag(flags, "layout_order_uncertain")
            candidate_reasons.append(
                "Reading order or layout confidence is too uncertain for direct freeze."
            )
        if ocr.noisy_text:
            if "ocr_low_confidence" not in flags:
                _add_flag(flags, "ocr_low_confidence")
            candidate_reasons.append(
                "OCR-like text noise suggests degraded extraction quality."
            )

        if _requires_candidate_by_source_type(
            request.source_type,
            request.source_metadata,
            markdown=markdown,
            ocr=ocr,
            metrics=metrics,
        ):
            candidate_reasons.append(
                f"{request.source_type} defaults to candidate review unless extraction confidence is explicitly high and the text is clearly simple."
            )

        reasons.extend(_dedupe_preserve_order(reject_reasons))
        reasons.extend(_dedupe_preserve_order(candidate_reasons))

        if reject_reasons:
            outcome: InputSuitabilityOutcome = "input_rejected_or_action_required"
        elif candidate_reasons:
            outcome = "candidate_document_required"
        else:
            outcome = "stable_document_ready"
            reasons.append(
                "Input has enough English natural-language content and no high-impact structure risks were detected."
            )

        score = _compute_natural_language_score(
            metrics=metrics,
            markdown=markdown,
            ocr=ocr,
            is_rejected=bool(reject_reasons),
            requires_candidate=bool(candidate_reasons),
        )
        return _build_result(
            outcome=outcome,
            request=request,
            word_count=metrics.word_count,
            english_word_ratio=metrics.english_word_ratio,
            natural_language_score=score,
            flags=flags,
            reasons=reasons,
            preview=preview,
        )


def evaluate_input_suitability(
    request: InputSuitabilityRequest,
) -> InputSuitabilityResult:
    return InputSuitabilityGate().evaluate(request)


def _build_result(
    *,
    outcome: InputSuitabilityOutcome,
    request: InputSuitabilityRequest,
    word_count: int,
    english_word_ratio: float,
    natural_language_score: float,
    flags: list[SourceLossFlag],
    reasons: list[str],
    preview: str,
) -> InputSuitabilityResult:
    return InputSuitabilityResult(
        outcome=outcome,
        source_type=request.source_type,
        word_count=word_count,
        english_word_ratio=round(english_word_ratio, 3),
        natural_language_score=round(natural_language_score, 3),
        flags=_dedupe_preserve_order(flags),
        reasons=_dedupe_preserve_order(reasons),
        normalized_preview=preview,
    )


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_preview(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= _MAX_PREVIEW_LENGTH:
        return compact
    return compact[: _MAX_PREVIEW_LENGTH - 1].rstrip() + "…"


def _measure_text(text: str) -> _TextMetrics:
    english_words = _ENGLISH_WORD_PATTERN.findall(text)
    tokens = _WORDLIKE_TOKEN_PATTERN.findall(text)
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    line_word_counts = [
        len(_WORDLIKE_TOKEN_PATTERN.findall(line))
        for line in nonempty_lines
    ]
    link_only_lines = [
        line for line in nonempty_lines if _LINK_ONLY_LINE_PATTERN.match(line)
    ]
    code_lines = [
        line for line in nonempty_lines if _looks_like_code_line(line)
    ]
    suspicious_chars = _SUSPICIOUS_OCR_CHAR_PATTERN.findall(text)

    word_count = len(tokens)
    english_word_count = len(english_words)
    english_word_ratio = (
        english_word_count / word_count if word_count > 0 else 0.0
    )
    nonempty_line_count = len(nonempty_lines)

    return _TextMetrics(
        word_count=word_count,
        english_word_count=english_word_count,
        english_word_ratio=english_word_ratio,
        sentence_count=len(_SENTENCE_END_PATTERN.findall(text)),
        nonempty_line_count=nonempty_line_count,
        link_only_line_ratio=(
            len(link_only_lines) / nonempty_line_count if nonempty_line_count else 0.0
        ),
        code_line_ratio=(
            len(code_lines) / nonempty_line_count if nonempty_line_count else 0.0
        ),
        short_line_ratio=(
            sum(1 for count in line_word_counts if 0 < count <= 4) / nonempty_line_count
            if nonempty_line_count
            else 0.0
        ),
        suspicious_ocr_char_ratio=(
            sum(len(match) for match in suspicious_chars) / len(text)
            if text
            else 0.0
        ),
    )


def _detect_markdown_complexity(
    text: str,
    *,
    source_type: InputAdapterSourceType,
    filename: str | None,
) -> _MarkdownComplexity:
    is_markdown_source = source_type == "markdown_file" or (
        filename is not None
        and filename.lower().endswith((".md", ".markdown"))
    )
    has_table = _has_markdown_table(text)
    has_image = bool(_MARKDOWN_IMAGE_PATTERN.search(text))
    has_footnote = bool(_MARKDOWN_FOOTNOTE_PATTERN.search(text))
    has_raw_html = bool(_HTML_PATTERN.search(text))
    has_math = bool(_INLINE_MATH_PATTERN.search(text) or _BLOCK_MATH_PATTERN.search(text))
    has_unclosed_fence = _has_unclosed_markdown_fence(text)
    has_simple_markdown = is_markdown_source and bool(
        _MARKDOWN_HEADING_PATTERN.search(text)
        or _MARKDOWN_LIST_PATTERN.search(text)
        or _MARKDOWN_BLOCKQUOTE_PATTERN.search(text)
    )
    has_complex_structure = any(
        (has_table, has_image, has_footnote, has_raw_html, has_math, has_unclosed_fence)
    )
    return _MarkdownComplexity(
        has_complex_structure=has_complex_structure,
        has_table=has_table,
        has_image=has_image,
        has_footnote=has_footnote,
        has_raw_html=has_raw_html,
        has_math=has_math,
        has_unclosed_fence=has_unclosed_fence,
        has_simple_markdown=has_simple_markdown,
    )


def _has_markdown_table(text: str) -> bool:
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if not _MARKDOWN_TABLE_ROW_PATTERN.match(line):
            continue
        separator = lines[index + 1]
        if not _is_markdown_table_separator(separator):
            continue
        if line.count("|") >= 2:
            return True
    return False


def _is_markdown_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped or "|" not in stripped or "-" not in stripped:
        return False
    if not all(char in {"|", ":", "-", " "} for char in stripped):
        return False
    return bool(re.search(r"-{3,}", stripped))


def _has_unclosed_markdown_fence(text: str) -> bool:
    in_fence = False
    fence_char: str | None = None
    fence_length = 0

    for line in text.splitlines():
        match = _CODE_FENCE_LINE_PATTERN.match(line)
        if match is None:
            continue

        marker = match.group(1)
        marker_char = marker[0]
        marker_length = len(marker)

        if in_fence:
            if marker_char == fence_char and marker_length >= fence_length:
                in_fence = False
                fence_char = None
                fence_length = 0
            continue

        in_fence = True
        fence_char = marker_char
        fence_length = marker_length

    return in_fence


def _detect_ocr_signals(
    text: str,
    *,
    source_type: InputAdapterSourceType,
    source_metadata: dict[str, Any],
    metrics: _TextMetrics,
) -> _OcrSignals:
    ocr_confidence = _first_float(
        source_metadata,
        "ocr_confidence",
        "ocr_text_confidence",
        "confidence",
    )
    extraction_confidence = _first_float(
        source_metadata,
        "extraction_confidence",
        "text_layer_confidence",
    )
    layout_confidence = _first_float(
        source_metadata,
        "layout_order_confidence",
        "reading_order_confidence",
        "order_confidence",
    )
    low_confidence = bool(
        source_metadata.get("ocr_low_confidence") is True
        or source_metadata.get("low_confidence") is True
        or (ocr_confidence is not None and ocr_confidence < _LOW_OCR_CONFIDENCE)
        or (
            source_type == "ocr_text"
            and extraction_confidence is not None
            and extraction_confidence < _HIGH_EXTRACTION_CONFIDENCE
        )
    )
    layout_uncertain = bool(
        source_metadata.get("layout_order_uncertain") is True
        or source_metadata.get("reading_order_uncertain") is True
        or source_metadata.get("multi_column") is True
        or (
            layout_confidence is not None and layout_confidence < _LOW_LAYOUT_CONFIDENCE
        )
    )
    noisy_text = bool(
        source_type == "ocr_text"
        and (
            metrics.suspicious_ocr_char_ratio >= 0.03
            or (
                metrics.nonempty_line_count >= 8
                and metrics.short_line_ratio >= 0.60
                and metrics.sentence_count <= 2
            )
            or _HYPHENATED_LINE_BREAK_PATTERN.search(text)
        )
    )
    return _OcrSignals(
        low_confidence=low_confidence,
        layout_uncertain=layout_uncertain,
        noisy_text=noisy_text,
    )


def _requires_candidate_by_source_type(
    source_type: InputAdapterSourceType,
    source_metadata: dict[str, Any],
    *,
    markdown: _MarkdownComplexity,
    ocr: _OcrSignals,
    metrics: _TextMetrics,
) -> bool:
    if source_type == "ocr_text":
        return True
    if source_type in {"pdf_text", "url_text"}:
        high_confidence = _first_float(
            source_metadata,
            "extraction_confidence",
            "text_layer_confidence",
        )
        has_explicit_high_confidence = (
            high_confidence is not None and high_confidence >= _HIGH_EXTRACTION_CONFIDENCE
        )
        clearly_simple = (
            not markdown.has_complex_structure
            and not ocr.low_confidence
            and not ocr.layout_uncertain
            and not ocr.noisy_text
            and metrics.link_only_line_ratio < 0.4
            and metrics.code_line_ratio < 0.25
        )
        return not (has_explicit_high_confidence and clearly_simple)
    return False


def _is_link_list_dominant(text: str, metrics: _TextMetrics) -> bool:
    url_count = len(_URL_PATTERN.findall(text))
    return bool(
        metrics.nonempty_line_count > 0
        and (
            metrics.link_only_line_ratio >= 0.50
            or (url_count >= 4 and metrics.english_word_count <= url_count * 3)
        )
    )


def _is_code_dominant(text: str, metrics: _TextMetrics) -> bool:
    fenced_code_line_ratio = _fenced_code_line_ratio(text)
    dominant_code_ratio = max(metrics.code_line_ratio, fenced_code_line_ratio)

    # A fenced block is only a rejection signal when code occupies a
    # substantial share of the document. Small illustrative snippets
    # inside an otherwise prose-heavy article should not be rejected as
    # code-dominant.
    if _CODE_FENCE_PATTERN.search(text):
        return bool(
            metrics.nonempty_line_count > 0
            and (
                dominant_code_ratio >= 0.55
                or (
                    dominant_code_ratio >= 0.35
                    and metrics.english_word_ratio < 0.85
                )
            )
        )
    return bool(
        metrics.nonempty_line_count > 0
        and (
            metrics.code_line_ratio >= 0.35
            or (
                metrics.code_line_ratio >= 0.25
                and metrics.english_word_ratio < 0.85
            )
        )
    )


def _fenced_code_line_ratio(text: str) -> float:
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    if not nonempty_lines:
        return 0.0

    fenced_line_count = 0
    in_fence = False
    fence_marker: str | None = None

    for line in nonempty_lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if in_fence and marker == fence_marker:
                in_fence = False
                fence_marker = None
            else:
                in_fence = True
                fence_marker = marker
            continue
        if in_fence:
            fenced_line_count += 1

    return fenced_line_count / len(nonempty_lines)


def _looks_like_code_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _CODE_LINE_PATTERN.search(stripped):
        return True
    punctuation_hits = sum(stripped.count(char) for char in "{}();=<>" if char in stripped)
    return punctuation_hits >= 3 and not _URL_PATTERN.search(stripped)


def _compute_natural_language_score(
    *,
    metrics: _TextMetrics,
    markdown: _MarkdownComplexity,
    ocr: _OcrSignals,
    is_rejected: bool,
    requires_candidate: bool,
) -> float:
    score = 0.25
    score += metrics.english_word_ratio * 0.45
    score += min(metrics.sentence_count / 4, 1.0) * 0.20
    score += max(0.0, 1.0 - metrics.short_line_ratio) * 0.10
    score -= min(metrics.link_only_line_ratio, 1.0) * 0.25
    score -= min(metrics.code_line_ratio, 1.0) * 0.30
    score -= min(metrics.suspicious_ocr_char_ratio * 4, 0.25)
    if markdown.has_complex_structure:
        score -= 0.10
    if ocr.low_confidence or ocr.layout_uncertain or ocr.noisy_text:
        score -= 0.10
    if requires_candidate:
        score -= 0.05
    if is_rejected:
        score -= 0.15
    return max(0.0, min(score, 1.0))


def _first_float(source_metadata: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = source_metadata.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _add_flag(flags: list[SourceLossFlag], flag: SourceLossFlag) -> None:
    if flag not in flags:
        flags.append(flag)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
