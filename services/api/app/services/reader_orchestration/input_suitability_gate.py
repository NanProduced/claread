from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from app.schemas.reader_input_adapter import (
    AdaptationRecord,
    DetectedInputFormat,
    InputAdapterSourceType,
    InputSuitabilityOutcome,
    InputSuitabilityRequest,
    InputSuitabilityResult,
    SourceLossFlag,
)
from app.services.reader_orchestration.input_format import (
    detect_input_format,
)
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownParseResult,
    MarkdownSourceParser,
)

_MARKDOWN_PARSER = MarkdownSourceParser()

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
# Strong out-of-spec code signals. The Markdown parser is the single
# source of truth for block structure, but a shebang or editor modeline
# is a hard cue that the input is a script even when the parser sees
# the raw lines as a paragraph. These patterns are intentionally narrow
# so legal citations like ``(2019);`` cannot match.
_SHEBANG_PATTERN = re.compile(r"^#!\s*/")
_MODELINE_PATTERN = re.compile(r"#\s*vim:\s*set|#\s*-\*-\s*coding|//\s*-\*-")
_INLINE_MATH_PATTERN = re.compile(r"(?<!\$)\$[^$\n]+\$(?!\$)")
_BLOCK_MATH_PATTERN = re.compile(r"\$\$[\s\S]+?\$\$")
# L2 — math 误判修复：``\[`` / ``\(`` 单独出现（如 ``\[Video]`` 转义
# 方括号、普通 prose 中的 ``\(2019)``）不再识别为数学公式。数学判定
# 要求**成对边界**（``\[`` 与 ``\]``、``\(`` 与 ``\)`` 配对）且内容
# 像公式（含 LaTeX 命令、``=``/``^``/``{``/``}`` 或数字-运算符-数字）。
_ESCAPED_MATH_PAIR_PATTERN = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]")
_MATHLIKE_CONTENT_PATTERN = re.compile(
    r"\\[a-zA-Z]+"  # LaTeX 命令（\frac / \sum ...）
    r"|[=^{}]"  # 等号 / 上下标 / 分组括号
    r"|\d\s*[+\-*/<>]\s*\d"  # 数字-运算符-数字（2x+3 中的 x+3 由字母规则覆盖）
    r"|[a-zA-Z]\s*[+\-*/=<>]\s*\d"  # 字母-运算符-数字（x+3 / E=mc2 的 c2 除外）
    r"|\d\s*[+\-*/=<>]\s*[a-zA-Z]"  # 数字-运算符-字母（2x）
)
_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_SUSPICIOUS_OCR_CHAR_PATTERN = re.compile(r"[�¦§¤]|[|]{2,}|[_]{3,}|[\\/]{3,}")
_HYPHENATED_LINE_BREAK_PATTERN = re.compile(r"[A-Za-z]-\n[A-Za-z]")

# Block types that count as Markdown prose structure for code-dominance
# detection. ``paragraph`` is included because the parser emits it for
# free-form narrative text; a code-only input fenced inside ``` has no
# paragraph blocks outside the fence.
_PROSE_BLOCK_TYPES = frozenset(
    {"heading", "paragraph", "list", "list_item", "blockquote", "table", "thematic_break"}
)


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
class _CodeStructureMetrics:
    """Parser-derived signals for code-dominance detection.

    Replaces the legacy hardcoded regex heuristics (``_CODE_LINE_PATTERN``,
    ``_looks_like_code_line``, ``_fenced_code_line_ratio``) with parser
    token signals that cannot misclassify legal citations like
    ``(2019);`` as code lines.
    """

    code_line_ratio: float
    prose_structure_count: int
    has_shebang_or_modeline: bool


@dataclass(frozen=True, slots=True)
class _MarkdownComplexity:
    has_complex_structure: bool
    has_table: bool
    has_table_structure_uncertain: bool
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
        *,
        preparsed: MarkdownParseResult | None = None,
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

        # A4 — 解析结果共享: when the caller has already parsed the
        # normalized text (e.g. the upload/materialization pipeline that
        # wants to share one parse across gate + normalizer + candidate
        # creation), reuse that result instead of invoking the parser
        # again. The caller is responsible for ensuring ``preparsed``
        # was produced from the same text (after ``_normalize_text``
        # normalization); we do not re-validate because the parser is
        # deterministic and the pipeline is single-threaded per request.
        parse_result = (
            preparsed
            if preparsed is not None
            else _MARKDOWN_PARSER.parse(normalized_text)
        )
        # L2 — 内容格式检测：与 source_type 正交，由 parser 块结构决定。
        # candidate / normalizer 路径共用同一判定与同一 parse_result。
        detected_format = detect_input_format(
            source_type=request.source_type,
            parse_result=parse_result,
        )
        code_metrics = _compute_code_structure_metrics(normalized_text, parse_result)
        metrics = _measure_text(normalized_text)
        metrics = replace(metrics, code_line_ratio=code_metrics.code_line_ratio)
        markdown = _detect_markdown_complexity(
            normalized_text,
            source_type=request.source_type,
            filename=request.filename,
            parse_result=parse_result,
        )
        ocr = _detect_ocr_signals(
            normalized_text,
            source_type=request.source_type,
            source_metadata=request.source_metadata,
            metrics=metrics,
        )

        reject_reasons: list[str] = []
        candidate_reasons: list[str] = []

        # code_dominant is computed early because prose-specific quality
        # checks (too_short_for_learning, non_english_or_mixed_language)
        # do not apply to code-dominant input. Such input is routed to
        # candidate review instead of being rejected for low English
        # prose volume or ratio, since those metrics measure prose
        # suitability, not code suitability.
        is_code_dominant = _is_code_dominant(code_metrics)

        if not is_code_dominant and metrics.english_word_count < _MIN_ENGLISH_WORDS:
            _add_flag(flags, "too_short_for_learning")
            reject_reasons.append(
                f"English content is too short for learning ({metrics.english_word_count} words)."
            )

        if metrics.word_count > _MAX_WORDS_BEFORE_ENVELOPE:
            _add_flag(flags, "too_long_requires_envelope")
            candidate_reasons.append(
                f"Input is too long to process as a single low-impact stable document ({metrics.word_count} words)."
            )

        if not is_code_dominant and metrics.english_word_ratio < _MIN_ENGLISH_WORD_RATIO:
            _add_flag(flags, "non_english_or_mixed_language")
            reject_reasons.append(
                f"English word ratio is too low ({metrics.english_word_ratio:.2f})."
            )

        if _is_link_list_dominant(normalized_text, metrics):
            _add_flag(flags, "link_list_dominant")
            reject_reasons.append("Input is dominated by links or URL-only lines.")

        if is_code_dominant:
            _add_flag(flags, "code_dominant")
            candidate_reasons.append(
                "输入疑似纯代码文本，缺少 Markdown 散文结构（标题/段落/列表），请在确认后继续。"
            )

        if markdown.has_table_structure_uncertain:
            # L1: only structure-uncertain tables (row/column mismatch the
            # parser would have to pad or drop cells for, or a missing
            # header row) require content check. Deterministic GFM tables
            # freeze as stable documents.
            _add_flag(flags, "markdown_complex_structure")
            _add_flag(flags, "table_structure_uncertain")
            candidate_reasons.append(
                "Markdown table structure is uncertain; deterministic "
                "normalization would drop or pad cells."
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
        # L1: raw / inline HTML no longer routes to candidate. The parser
        # strips executable structure and preserves the text as an
        # ``adaptation_notice``; the document continues.
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

        source_type_requires_candidate = _requires_candidate_by_source_type(
            request.source_type,
            request.source_metadata,
            markdown=markdown,
            ocr=ocr,
            metrics=metrics,
        )
        if source_type_requires_candidate:
            candidate_reasons.append(
                f"{request.source_type} defaults to candidate review unless extraction confidence is explicitly high and the text is clearly simple."
            )

        reasons.extend(_dedupe_preserve_order(reject_reasons))
        reasons.extend(_dedupe_preserve_order(candidate_reasons))

        # L1: structured three-level adaptation output. Parser warnings
        # flow through with their authoritative classification; gate-only
        # signals are recorded as content_check entries.
        adaptations = _build_adaptations(
            parse_result=parse_result,
            markdown=markdown,
            ocr=ocr,
            is_code_dominant=is_code_dominant,
            word_count=metrics.word_count,
            source_type_requires_candidate=source_type_requires_candidate,
            source_type=request.source_type,
        )

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
            adaptations=adaptations,
            detected_format=detected_format,
        )


def evaluate_input_suitability(
    request: InputSuitabilityRequest,
    *,
    preparsed: MarkdownParseResult | None = None,
) -> InputSuitabilityResult:
    return InputSuitabilityGate().evaluate(request, preparsed=preparsed)


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
    adaptations: list[AdaptationRecord] | None = None,
    detected_format: DetectedInputFormat = "plain_text",
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
        adaptations=adaptations or [],
        detected_format=detected_format,
    )


def _build_adaptations(
    *,
    parse_result: MarkdownParseResult,
    markdown: _MarkdownComplexity,
    ocr: _OcrSignals,
    is_code_dominant: bool,
    word_count: int,
    source_type_requires_candidate: bool,
    source_type: InputAdapterSourceType,
) -> list[AdaptationRecord]:
    """Assemble the L1 three-level adaptation records.

    Parser warnings keep their authoritative classification. Gate-only
    detections (image / math / OCR / code dominance / length / source-type
    defaults) are always content_check because they require human review.
    """
    adaptations: list[AdaptationRecord] = []
    seen: set[str] = set()

    def _add(code: str, message: str, classification: str) -> None:
        if code in seen:
            return
        seen.add(code)
        adaptations.append(
            AdaptationRecord(
                code=code,
                message=message,
                classification=classification,  # type: ignore[arg-type]
            )
        )

    for warning in parse_result.warnings:
        _add(warning.code, warning.message, warning.classification)

    if markdown.has_image:
        _add(
            "image_ocr_uncertain",
            "Markdown image blocks require candidate review so media truth is not lost.",
            "content_check",
        )
    if markdown.has_math:
        _add(
            "document_block_degraded",
            "Math syntax requires candidate review instead of deterministic downgrade.",
            "content_check",
        )
    if is_code_dominant:
        _add(
            "code_dominant",
            "Input appears to be code-dominant without Markdown prose structure.",
            "content_check",
        )
    if word_count > _MAX_WORDS_BEFORE_ENVELOPE:
        _add(
            "too_long_requires_envelope",
            "Input is too long to process as a single low-impact stable document.",
            "content_check",
        )
    if ocr.low_confidence or ocr.noisy_text:
        _add(
            "ocr_low_confidence",
            "OCR confidence or text noise suggests degraded extraction quality.",
            "content_check",
        )
    if ocr.layout_uncertain:
        _add(
            "layout_order_uncertain",
            "Reading order or layout confidence is too uncertain for direct freeze.",
            "content_check",
        )
    if source_type_requires_candidate:
        _add(
            "source_type_review_default",
            f"{source_type} defaults to candidate review unless extraction "
            "confidence is explicitly high and the text is clearly simple.",
            "content_check",
        )
    return adaptations


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
        # code_line_ratio is populated from parser signals in evaluate()
        # via _CodeStructureMetrics + dataclasses.replace; the placeholder
        # is 0.0 so _measure_text can run before the parser is invoked.
        code_line_ratio=0.0,
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


def _has_math_syntax(text: str) -> bool:
    """L2 — 数学公式判定：成对边界 + 内容像公式，或 parser 级别信号。

    - ``$...$`` / ``$$...$$``：本身即要求成对，直接采信。
    - ``\\(...\\)`` / ``\\[...\\]``：必须成对出现，且内部内容像公式
      （LaTeX 命令、``=``/``^``/``{``/``}``、数字与运算符组合）。
      单独的 ``\\[Video]`` 转义方括号、``\\(2019)`` 引用、未成对的
      ``\\(`` 不再误判为数学公式。
    """
    if _INLINE_MATH_PATTERN.search(text) or _BLOCK_MATH_PATTERN.search(text):
        return True
    for match in _ESCAPED_MATH_PAIR_PATTERN.finditer(text):
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        if inner and _MATHLIKE_CONTENT_PATTERN.search(inner):
            return True
    return False


def _detect_markdown_complexity(
    text: str,
    *,
    source_type: InputAdapterSourceType,
    filename: str | None,
    parse_result: MarkdownParseResult,
) -> _MarkdownComplexity:
    is_markdown_source = source_type == "markdown_file" or (
        filename is not None
        and filename.lower().endswith((".md", ".markdown"))
    )
    # Derive structural flags from the parser adapter instead of raw-text
    # regex. The parser is the single source of truth for block structure
    # (tables, footnotes, raw HTML, unclosed fences); image and math are
    # inline features the parser flattens without flagging, so they stay
    # on lightweight regex probes.
    block_types = {block.block_type for block in parse_result.blocks}
    warning_codes = {warning.code for warning in parse_result.warnings}

    has_table = "table" in block_types
    has_table_structure_uncertain = "table_structure_uncertain" in warning_codes
    has_image = bool(_MARKDOWN_IMAGE_PATTERN.search(text))
    has_footnote = (
        "footnote_reference" in warning_codes
        or "footnote" in block_types
    )
    has_raw_html = (
        "raw_html_block" in warning_codes
        or "inline_html" in warning_codes
    )
    has_math = _has_math_syntax(text)
    has_unclosed_fence = "has_unclosed_fence" in warning_codes
    has_simple_markdown = is_markdown_source and bool(
        block_types & {"heading", "list", "list_item", "blockquote"}
    )
    # L1: deterministic tables and cleaned raw HTML are no longer
    # "complex" for candidate-routing purposes (they continue as stable
    # with adaptation notices); they still count as structure for the
    # natural-language score and the pdf/url "clearly simple" probe.
    has_complex_structure = any(
        (
            has_table_structure_uncertain,
            has_image,
            has_footnote,
            has_raw_html,
            has_math,
            has_unclosed_fence,
        )
    )
    return _MarkdownComplexity(
        has_complex_structure=has_complex_structure,
        has_table=has_table,
        has_table_structure_uncertain=has_table_structure_uncertain,
        has_image=has_image,
        has_footnote=has_footnote,
        has_raw_html=has_raw_html,
        has_math=has_math,
        has_unclosed_fence=has_unclosed_fence,
        has_simple_markdown=has_simple_markdown,
    )


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


def _compute_code_structure_metrics(
    text: str,
    parse_result: MarkdownParseResult,
) -> _CodeStructureMetrics:
    """Derive code-dominance signals from parser tokens.

    Replaces the legacy hardcoded regex heuristics. The parser is the
    single source of truth for block structure, so legal citations like
    ``(2019);`` are correctly classified as prose (paragraph blocks)
    rather than code lines.

    - ``code_line_ratio``: sum of ``code_block`` source_range line spans
      divided by total nonempty lines (capped at 1.0).
    - ``prose_structure_count``: count of prose block types
      (heading/paragraph/list/list_item/blockquote/table/thematic_break).
    - ``has_shebang_or_modeline``: shebang on the first nonempty line or
      an editor modeline anywhere in the text.
    """
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    total_nonempty = len(nonempty_lines)

    code_line_span = 0
    prose_structure_count = 0
    for block in parse_result.blocks:
        if block.block_type == "code_block":
            code_line_span += (
                block.source_range.line_end - block.source_range.line_start + 1
            )
        if block.block_type in _PROSE_BLOCK_TYPES:
            prose_structure_count += 1

    code_line_ratio = (
        min(code_line_span / total_nonempty, 1.0) if total_nonempty > 0 else 0.0
    )

    has_shebang_or_modeline = False
    if nonempty_lines and _SHEBANG_PATTERN.match(nonempty_lines[0]):
        has_shebang_or_modeline = True
    if not has_shebang_or_modeline:
        for line in nonempty_lines:
            if _MODELINE_PATTERN.search(line):
                has_shebang_or_modeline = True
                break

    return _CodeStructureMetrics(
        code_line_ratio=code_line_ratio,
        prose_structure_count=prose_structure_count,
        has_shebang_or_modeline=has_shebang_or_modeline,
    )


def _is_code_dominant(code_metrics: _CodeStructureMetrics) -> bool:
    """Determine if the input is code-dominant using parser token signals.

    Three independent triggers:
    1. Shebang or editor modeline — strong out-of-spec signal that the
       input is a script, even when the parser sees raw code as a paragraph.
    2. No prose structure (heading/paragraph/list/...) and code occupies
       at least half the nonempty lines — a pure code blob.
    3. Code occupies >= 80% of lines and prose structure is minimal (<= 1
       block) — heavily code-saturated input with trivial prose wrapper.

    Multiple headings (prose_structure_count > 1) protect legitimate
    Markdown articles that contain large code samples from being flagged.
    """
    if code_metrics.has_shebang_or_modeline:
        return True
    if code_metrics.prose_structure_count == 0 and code_metrics.code_line_ratio >= 0.5:
        return True
    if code_metrics.code_line_ratio >= 0.8 and code_metrics.prose_structure_count <= 1:
        return True
    return False


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
