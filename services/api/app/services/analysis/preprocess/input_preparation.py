"""
输入预处理分层重构 (v3)。

快路径优先、重路径兜底的分层设计：
1. 换行归一化（layer1）
2. ftfy 最小修复（layer2）
3. BeautifulSoup HTML 清理 + 结构检测（layer3）
4. langdetect 语言检测（layer4）
5. 段落切分 + spaCy 断句 / 改进 regex 兜底（layer5）
6. 质量检测（layer6）
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.schemas.common import TextSpan
from app.schemas.internal.analysis import (
    PreparedInput,
    PreparedParagraph,
    PreparedSentence,
    SanitizeReport,
)

if TYPE_CHECKING:
    import spacy.language

__all__ = ["prepare_input", "PreparedInput"]
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level regex patterns (kept for backward-compatible sanitize_text)
# ---------------------------------------------------------------------------

CODE_FENCE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_PATTERN = re.compile(r"`[^`\n]+`")
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
URL_PATTERN = re.compile(r"https?://[^\s]+|www\.[^\s]+")
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
HTML_BLOCK_BREAK_PATTERN = re.compile(
    r"</?(?:p|div|section|article|li|ul|ol|br|h[1-6])[^>]*>",
    re.IGNORECASE,
)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
CONTROL_CHAR_PATTERN = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
MULTISPACE_PATTERN = re.compile(r"[ \t]+")
LATIN_LETTER_PATTERN = re.compile(r"[A-Za-z]")
ALPHANUMERIC_PATTERN = re.compile(r"[A-Za-z0-9]")

# Improved regex for regex-fallback sentence splitting
# Uses only ASCII quotes to avoid encoding issues
# Abbreviation regex: uses leading \b only (trailing \b doesn't work for
# abbreviations ending in '.' followed by a space — no word boundary exists there).
# Patterns must end with '.' to distinguish abbreviations from regular words.
_ABBREVIATION_RE = re.compile(
    r"\b("
    r"U\.S\.|U\.K\.|Ph\.D\.|"
    r"e\.g\.|i\.e\.|"  # i.e. fixed: was (?:e|i)\.g\. which matched i.g. and missed i.e.
    r"Dr\.|Mr\.|Mrs\.|Ms\.|Prof\.|Sr\.|Jr\.|"
    r"Dept\.|Govt\.|Est\.|Inc\.|Ltd\.|Corp\.|vs\.|"
    r"approx\.|etc\."
    r")",
    re.IGNORECASE,
)
_IMPROVED_SENTENCE_PATTERN = re.compile(
    r".+?(?:[.!?](?:\"|')?(?=\s+[A-Z][a-z])|$)",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StructureHint:
    """结构检测结果，供后续各层消费。"""

    has_html_tags: bool
    html_tag_count: int
    has_code_fences: bool
    bullet_density: float
    cjk_ratio: float
    text_type: str  # "article_en" | "article_mixed" | "structured_doc" | "html_like" | "code_like" | "other"


@dataclass(frozen=True, slots=True)
class _PipelineResult:
    """6 层 pipeline 的最终结果。"""

    render_text: str
    paragraphs: list[PreparedParagraph]
    sentences: list[PreparedSentence]
    sanitize_report: SanitizeReport
    english_ratio: float
    noise_ratio: float
    text_type: str
    fast_path: bool
    language_detected: str | None
    fallback_reason: str | None


# ---------------------------------------------------------------------------
# Layer 1: preserve source + line ending normalization
# ---------------------------------------------------------------------------


def layer1_preserve_source(source_text: str) -> tuple[str, list[str]]:
    """换行归一化：\\r\\n -> \\n，\\r -> \\n。"""
    actions: list[str] = []
    text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    if text != source_text:
        actions.append("normalize_line_breaks")
    return text, actions


# ---------------------------------------------------------------------------
# Layer 2: ftfy minimal fix
# ---------------------------------------------------------------------------


def layer2_minimal_fix(text: str) -> tuple[str, list[str]]:
    """
    ftfy 最小修复：仅在文本存在编码问题时调用。
    well-formed 文本（bad_rating < 0.1）直接跳过，节省开销。
    """
    actions: list[str] = []
    try:
        import ftfy

        rating = ftfy.badness.bad_rating(text)
        if rating < 0.1:
            return text, actions
    except Exception as e:
        logging.getLogger(__name__).debug(f"ftfy.bad_rating unavailable: {e}")

    try:
        import ftfy

        fixed = ftfy.fix_text(text)
        if fixed != text:
            actions.append("ftfy_fix")
        return fixed, actions
    except Exception as e:
        logging.getLogger(__name__).debug(f"ftfy.fix_text unavailable, returning original: {e}")
        return text, actions


# ---------------------------------------------------------------------------
# Layer 3: structure detection (BeautifulSoup + heuristics)
# ---------------------------------------------------------------------------


def layer3_detect_structure(text: str) -> tuple[_StructureHint, list[str]]:
    """
    早期结构识别：区分自然正文、HTML、代码、结构化文档。
    使用 BeautifulSoup 检测 HTML 标签密度，配合启发式判断。
    """
    actions: list[str] = []
    has_code_fences = bool(CODE_FENCE_PATTERN.search(text))

    # BeautifulSoup HTML 检测
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        all_tags = soup.find_all(True)
        html_tag_count = len(all_tags)
        has_html_tags = html_tag_count > 2
    except Exception as e:
        logging.getLogger(__name__).debug(f"BeautifulSoup HTML detection unavailable: {e}")
        has_html_tags = False
        html_tag_count = 0

    # Bullet density
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    bullet_count = sum(1 for ln in lines if ln.startswith(("-", "*", chr(0x2022))))
    bullet_density = bullet_count / len(lines) if lines else 0.0

    # CJK ratio
    cjk_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    cjk_ratio = cjk_chars / len(text) if text else 0.0

    # Text type inference (6-value)
    if has_code_fences or ("{" in text and "}" in text and ";" in text):
        inferred_type = "code_like"
    elif has_html_tags and html_tag_count > 5:
        inferred_type = "html_like"
    elif bullet_density > 0.5:
        inferred_type = "structured_doc"
    elif cjk_ratio > 0.3:
        inferred_type = "article_mixed"
    elif len(ALPHANUMERIC_PATTERN.findall(text)) < 20:
        inferred_type = "other"
    else:
        inferred_type = "article_en"

    hint = _StructureHint(
        has_html_tags=has_html_tags,
        html_tag_count=html_tag_count,
        has_code_fences=has_code_fences,
        bullet_density=bullet_density,
        cjk_ratio=cjk_ratio,
        text_type=inferred_type,
    )
    return hint, actions


# ---------------------------------------------------------------------------
# Layer 4: language detection
# ---------------------------------------------------------------------------


def layer4_detect_language(
    text: str,
    hint: _StructureHint,
) -> tuple[str | None, float, list[str]]:
    """
    使用 langdetect 检测主语言。
    structured_doc / code_like 跳过（浪费且不可靠）。
    太短文本（<50 chars）跳过。
    """
    actions: list[str] = []
    english_ratio = _english_ratio(text)

    if hint.text_type in ("structured_doc", "code_like"):
        return None, english_ratio, actions

    if len(text.strip()) < 50:
        actions.append("langdetect_skip_too_short")
        return None, english_ratio, actions

    try:
        from langdetect import detect_langs

        langs = list(detect_langs(text))
        if langs:
            dominant = langs[0].lang
            if dominant != "en":
                actions.append(f"langdetect={dominant}")
            return dominant, english_ratio, actions
    except Exception as e:
        logging.getLogger(__name__).debug(f"langdetect unavailable, defaulting to 'en': {e}")

    return "en", english_ratio, actions


# ---------------------------------------------------------------------------
# Fast-path eligibility
# ---------------------------------------------------------------------------


def _is_fast_path_eligible(
    hint: _StructureHint,
    english_ratio: float,
    text: str,
) -> tuple[bool, str | None]:
    """
    判断是否走快路径（spaCy 断句）。

    核心原则："是否值得分析" 和 "是否值得用 spaCy 分句" 分开判断。
    即使是短英文正文，只要结构清晰、英文比例不太低，就值得 spaCy 分句。

    条件满足才走快路径；否则走兜底路径（regex）。
    """
    # 结构异常：不值得用 spaCy 做自然语言处理
    if hint.has_html_tags and len(text) > 200:
        return False, "html_detected"
    if hint.has_code_fences:
        return False, "code_fence_detected"
    if hint.text_type == "code_like":
        return False, "code_like"
    if hint.text_type == "structured_doc":
        return False, "structured_doc"
    if hint.text_type == "article_mixed":
        return False, "mixed_language"

    # 英文比例门槛降低：只要有 50% 以上英文字符，就值得 spaCy 处理
    # （原来 0.85 太高，把很多中英混合正文中的英文片段也挡掉了）
    if english_ratio < 0.50:
        return False, f"low_english_ratio_{english_ratio:.2f}"

    # 噪声率：过高说明文本本身有问题
    noise_estimate = sum(
        1 for c in text if not c.isalnum() and not c.isspace()
    ) / max(len(text), 1)
    if noise_estimate > 0.5:
        return False, f"too_noisy_{noise_estimate:.2f}"

    return True, None


def _is_heading_like_line(line: str, next_line: str) -> bool:
    """
    Heuristic: detect a short standalone heading line at the start of a paragraph.

    Used to split inputs like:
        April Fool's traditions
        In the UK, jokes and tricks ...

    without breaking normal wrapped body text.
    """
    stripped = line.strip()
    next_stripped = next_line.strip()
    if not stripped or not next_stripped:
        return False

    # Headings are short and compact.
    if len(stripped) > 60:
        return False
    if len(stripped.split()) > 8:
        return False

    # A heading line should not already look like a sentence.
    if stripped[-1:] in {".", "?", "!", ":", ";", ","}:
        return False
    if any(ch in stripped for ch in [".", "?", "!", ";", ","]):
        return False

    # The following line should look like real prose.
    if len(next_stripped) < 20:
        return False
    if not next_stripped[0].isupper():
        return False

    return True


def _split_paragraph_spans(render_text: str) -> list[tuple[int, int]]:
    """
    Split paragraphs by blank lines first, then apply a heading-aware split
    for blocks that begin with a short standalone title line.
    """
    base_spans: list[tuple[int, int]] = []
    offset = 0
    for para_block in render_text.split("\n\n"):
        stripped = para_block.strip()
        if not stripped:
            continue
        para_start = render_text.find(stripped, offset)
        if para_start == -1:
            para_start = offset
        para_end = para_start + len(stripped)
        offset = para_end
        base_spans.append((para_start, para_end))

    paragraph_spans: list[tuple[int, int]] = []
    for para_start, para_end in base_spans:
        para_text = render_text[para_start:para_end]
        if "\n" not in para_text:
            paragraph_spans.append((para_start, para_end))
            continue

        first_line, remainder = para_text.split("\n", 1)
        next_line = remainder.split("\n", 1)[0] if remainder else ""

        if _is_heading_like_line(first_line, next_line):
            heading_end = para_start + len(first_line)
            remainder_start = heading_end + 1
            remainder_text = para_text[len(first_line) + 1 :].strip()

            paragraph_spans.append((para_start, heading_end))
            if remainder_text:
                abs_remainder_start = render_text.find(remainder_text, remainder_start)
                if abs_remainder_start == -1:
                    abs_remainder_start = remainder_start
                paragraph_spans.append(
                    (abs_remainder_start, abs_remainder_start + len(remainder_text))
                )
        else:
            paragraph_spans.append((para_start, para_end))

    return paragraph_spans


# ---------------------------------------------------------------------------
# Layer 5: paragraph + sentence splitting
# ---------------------------------------------------------------------------

# Module-level spaCy singleton (lazy init) + model availability tracking
_nlp: object | None = None
_spacy_available: bool | None = None  # None = unchecked, True = available, False = unavailable
_spacy_checked: bool = False


def _check_spacy_model() -> bool:
    """Check if spaCy and en_core_web_sm are available. Result is cached."""
    global _spacy_available, _spacy_checked
    if _spacy_checked:
        return _spacy_available
    _spacy_checked = True
    try:
        import spacy

        nlp = spacy.load(
            "en_core_web_sm",
            disable=["ner", "lemmatizer", "tagger"],
        )
        nlp.enable_pipe("senter")
        _spacy_available = True
        logger.info("prepare_input: spaCy model en_core_web_sm loaded successfully")
        return True
    except (ImportError, OSError, Exception) as e:
        _spacy_available = False
        logger.warning(
            f"prepare_input: spaCy model en_core_web_sm unavailable: {e}. "
            "Install with: python -m spacy download en_core_web_sm"
        )
        return False


def _get_spacy_nlp() -> spacy.language.Language:
    """Lazy-load spaCy model. MUST only be called after _check_spacy_model() returns True."""
    global _nlp
    if _nlp is None:
        import spacy

        nlp = spacy.load("en_core_web_sm", disable=["ner", "lemmatizer", "tagger"])
        nlp.enable_pipe("senter")  # sentence boundary detector
        _nlp = nlp
    return _nlp


def _split_sentences_spacy(
    paragraph_texts: list[str],
) -> list[tuple[int, int, int]]:
    """
    spaCy nlp.pipe() 批量断句。

    Returns:
        List of (start_char, end_char, para_idx) where offsets are RELATIVE
        to the start of each paragraph text.
    """
    nlp = _get_spacy_nlp()
    result: list[tuple[int, int, int]] = []
    for para_idx, doc in enumerate(nlp.pipe(paragraph_texts, batch_size=10)):
        for sent in doc.sents:
            result.append((sent.start_char, sent.end_char, para_idx))
    return result


def _split_sentences_regex(
    render_text: str,
    paragraph_spans: list[tuple[int, int]],
) -> list[PreparedSentence]:
    """兜底断句：带缩写保护的改进 regex，在 spaCy 不可用时使用。"""
    sentences: list[PreparedSentence] = []

    def _protect_abbreviations(text: str) -> tuple[str, dict[str, str]]:
        """临时替换已知缩写，避免断句时在缩写句号处错误断开。"""
        placeholders: dict[str, str] = {}
        counter = 0

        def _replace(match: re.Match) -> str:
            nonlocal counter
            placeholder = f"__ABB{counter}__"
            placeholders[placeholder] = match.group(0)
            counter += 1
            return placeholder

        protected = _ABBREVIATION_RE.sub(_replace, text)
        return protected, placeholders

    def _restore_abbreviations(text: str, placeholders: dict[str, str]) -> str:
        """恢复被保护的缩写。"""
        result = text
        for placeholder, original in sorted(placeholders.items(), key=lambda x: len(x[0]), reverse=True):
            result = result.replace(placeholder, original)
        return result

    for para_idx, (para_start, para_end) in enumerate(paragraph_spans, 1):
        para_text = render_text[para_start:para_end]

        # Abbreviation protection
        protected_text, placeholders = _protect_abbreviations(para_text)

        matches = [
            m.group(0).strip()
            for m in _IMPROVED_SENTENCE_PATTERN.finditer(protected_text)
            if m.group(0).strip()
        ]

        sent_offset = para_start
        for sent_text in (matches or [protected_text.strip()]):
            if not sent_text:
                continue
            # Find original text position
            # Use the protected text to find offset, then map back
            restored_sent = _restore_abbreviations(sent_text, placeholders)
            sent_start = render_text.find(restored_sent, sent_offset)
            if sent_start == -1:
                sent_start = sent_offset
            sent_end = sent_start + len(restored_sent)
            sentences.append(
                PreparedSentence(
                    sentence_id=f"s{len(sentences) + 1}",
                    paragraph_id=f"p{para_idx}",
                    text=restored_sent,
                    sentence_span=TextSpan(start=sent_start, end=sent_end),
                )
            )
            sent_offset = sent_end
    return sentences


def _build_sentences_from_spacy_spans(
    render_text: str,
    paragraph_spans: list[tuple[int, int]],
    sent_rel_spans: list[tuple[int, int, int]],
) -> list[PreparedSentence]:
    """将 spaCy 返回的相对 offset 转换为绝对 offset，构建 PreparedSentence。"""
    sentences: list[PreparedSentence] = []
    for sent_start_rel, sent_end_rel, para_idx in sent_rel_spans:
        para_start, _ = paragraph_spans[para_idx]
        abs_start = para_start + sent_start_rel
        abs_end = para_start + sent_end_rel

        while abs_start < abs_end and render_text[abs_start].isspace():
            abs_start += 1
        while abs_end > abs_start and render_text[abs_end - 1].isspace():
            abs_end -= 1

        sentence_text = render_text[abs_start:abs_end]
        sentences.append(
            PreparedSentence(
                sentence_id=f"s{len(sentences) + 1}",
                paragraph_id=f"p{para_idx + 1}",
                text=sentence_text,
                sentence_span=TextSpan(start=abs_start, end=abs_end),
            )
        )
    return sentences


def layer5_split(
    render_text: str,
    fast_path: bool,
    forced_regex_action: str | None = None,
) -> tuple[list[PreparedParagraph], list[PreparedSentence], list[str]]:
    """
    段落切分（始终以 \\n\\n 划分）和句子切分。

    fast_path=True  -> spaCy 批量断句（正确处理 U.S. / e.g. / Dr. 等缩写）
                       spaCy 不可用时显式降级到 regex
    fast_path=False -> 改进 regex 兜底（缩写已保护）

    forced_regex_action: 当需要强制走 regex 但又需要区分原因时传入。
                          必须是以下值之一：
                            - "regex_sentence_split_no_spacy"  (spaCy 模型不可用)
                            - "regex_sentence_split_spacy_error" (spaCy 运行时错误)
                          传入此值时，action 记录该值而非通用 "regex_sentence_split"。
    """
    actions: list[str] = []

    # ---- Paragraph split (always by blank lines) ----
    paragraph_spans = _split_paragraph_spans(render_text)

    # ---- Sentence split ----
    if fast_path:
        paragraph_texts = [render_text[s:e] for s, e in paragraph_spans]
        if _spacy_available is False:
            # spaCy 模型不可用：显式降级，可观测
            import logging

            logging.getLogger(__name__).warning(
                "spaCy unavailable, falling back to regex sentence splitting"
            )
            sentences = _split_sentences_regex(render_text, paragraph_spans)
            actions.append("regex_sentence_split_no_spacy")
        else:
            # spaCy available (or not yet checked), try it
            try:
                # Ensure model is checked before loading
                if _spacy_available is None:
                    _check_spacy_model()
                if _spacy_available:
                    sent_rel_spans = _split_sentences_spacy(paragraph_texts)
                    sentences = _build_sentences_from_spacy_spans(
                        render_text, paragraph_spans, sent_rel_spans
                    )
                    actions.append("spacy_sentence_split")
                else:
                    # Model check ran and said unavailable
                    sentences = _split_sentences_regex(render_text, paragraph_spans)
                    actions.append("regex_sentence_split_no_spacy")
            except Exception as e:
                # spaCy runtime error: 显式降级，可观测
                import logging

                logging.getLogger(__name__).warning(
                    f"spaCy runtime error: {e}, falling back to regex sentence splitting"
                )
                sentences = _split_sentences_regex(render_text, paragraph_spans)
                actions.append("regex_sentence_split_spacy_error")
    elif forced_regex_action:
        # 强制 regex，但记录显式原因（spaCy 不可用导致的降级路径）
        sentences = _split_sentences_regex(render_text, paragraph_spans)
        actions.append(forced_regex_action)
    else:
        sentences = _split_sentences_regex(render_text, paragraph_spans)
        actions.append("regex_sentence_split")

    logger.info(
        "prepare_input: layer5_split completed",
        extra={
            "paragraph_count": len(paragraph_spans),
            "sentence_count": len(sentences),
            "fast_path": fast_path,
            "forced_regex_action": forced_regex_action,
            "split_actions": actions,
        },
    )

    # ---- Build PreparedParagraph with sentence_ids ----
    sent_idx_by_para: dict[int, list[str]] = {
        i: [] for i in range(1, len(paragraph_spans) + 1)
    }
    for sent in sentences:
        pnum = int(sent.paragraph_id[1:])
        if pnum in sent_idx_by_para:
            sent_idx_by_para[pnum].append(sent.sentence_id)

    paragraphs: list[PreparedParagraph] = []
    for i, (s, e) in enumerate(paragraph_spans, 1):
        paragraphs.append(
            PreparedParagraph(
                paragraph_id=f"p{i}",
                text=render_text[s:e],
                render_span=TextSpan(start=s, end=e),
                sentence_ids=sent_idx_by_para.get(i, []),
            )
        )

    return paragraphs, sentences, actions


# ---------------------------------------------------------------------------
# Layer 6: quality detection
# ---------------------------------------------------------------------------


def layer6_quality_detection(
    english_ratio: float,
    noise_ratio: float,
    sentence_count: int,
    text_length: int,
) -> tuple[bool, float, list[str]]:
    """
    质量门槛检测。返回 (passes_quality, noise_ratio, actions)。
    不通过的输入仍生成结果，只是给下游 warning。
    """
    actions: list[str] = []
    if english_ratio < 0.10:
        actions.append("quality_fail_non_english")
        return False, noise_ratio, actions
    if noise_ratio > 0.70:
        actions.append("quality_fail_too_noisy")
        return False, noise_ratio, actions
    if text_length < 50:
        actions.append("quality_fail_too_short")
        return False, noise_ratio, actions
    if sentence_count == 0:
        actions.append("quality_fail_no_sentences")
        return False, noise_ratio, actions
    return True, noise_ratio, actions


# ---------------------------------------------------------------------------
# Refactored sanitize_text() - BeautifulSoup replaces HTML regex
# ---------------------------------------------------------------------------
# Signature preserved: (source_text: str) -> (render_text, SanitizeReport)


def sanitize_text(source_text: str) -> tuple[str, SanitizeReport]:
    """
    重构版 sanitize_text：BeautifulSoup 替换 HTML 标签正则，更稳健。
    签名完全向后兼容：保留原有 action 名称（remove_url, remove_code_fence 等）。
    """
    actions: list[str] = []
    removed_segment_count = 0

    # 1. 换行归一化
    text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    if text != source_text:
        actions.append("normalize_line_breaks")

    # 2. HTML entity 解码
    text = html.unescape(text)

    # 3. 代码块
    code_fence_count = len(CODE_FENCE_PATTERN.findall(text))
    text = CODE_FENCE_PATTERN.sub("\n", text)
    if code_fence_count:
        actions.append("remove_code_fence")
        removed_segment_count += code_fence_count

    # 4. 行内代码
    inline_code_count = len(INLINE_CODE_PATTERN.findall(text))
    text = INLINE_CODE_PATTERN.sub(" ", text)
    if inline_code_count:
        actions.append("remove_inline_code")
        removed_segment_count += inline_code_count

    # 5. Markdown 链接（保留文字部分）
    md_link_count = len(MARKDOWN_LINK_PATTERN.findall(text))
    text = MARKDOWN_LINK_PATTERN.sub(r"\1", text)
    if md_link_count:
        actions.append("strip_markdown_link_url")
        removed_segment_count += md_link_count

    # 6. HTML 清理 - BeautifulSoup 替换 HTML_BLOCK_BREAK + HTML_TAG regex
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        # 记录标签数量（用于 report）
        all_tags = soup.find_all(True)
        html_tag_count = len(all_tags)
        # block-level 标签转换行后再 unwrap
        for tag in all_tags:
            if tag.name in {
                "br", "p", "div", "section", "article",
                "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
            }:
                tag.append("\n")
            tag.unwrap()
        text = soup.get_text(separator="")
        if html_tag_count > 0:
            actions.append("bs4_html_processing")
            removed_segment_count += html_tag_count
    except Exception as e:
        logging.getLogger(__name__).debug(f"BeautifulSoup HTML processing unavailable, using regex fallback: {e}")
        html_break_count = len(HTML_BLOCK_BREAK_PATTERN.findall(text))
        text = HTML_BLOCK_BREAK_PATTERN.sub("\n", text)
        html_tag_count_fallback = len(HTML_TAG_PATTERN.findall(text))
        text = HTML_TAG_PATTERN.sub(" ", text)
        if html_tag_count_fallback > 0:
            actions.append("remove_html_tags_regex_fallback")
            removed_segment_count += html_tag_count_fallback
        if html_break_count > 0:
            removed_segment_count += html_break_count

    # 7. URL
    url_count = len(URL_PATTERN.findall(text))
    text = URL_PATTERN.sub(" ", text)
    if url_count:
        actions.append("remove_url")
        removed_segment_count += url_count

    # 8. Email
    email_count = len(EMAIL_PATTERN.findall(text))
    text = EMAIL_PATTERN.sub(" ", text)
    if email_count:
        actions.append("remove_email")
        removed_segment_count += email_count

    # 9. 控制字符
    text = CONTROL_CHAR_PATTERN.sub("", text)

    # 10. 空格与换行归一化
    # Preserve blank lines so paragraph boundaries survive into layer5_split().
    lines = [MULTISPACE_PATTERN.sub(" ", ln).strip() for ln in text.split("\n")]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if cleaned != text:
        actions.append("collapse_spaces")

    return cleaned, SanitizeReport(actions=actions, removed_segment_count=removed_segment_count)


# ---------------------------------------------------------------------------
# Internal helpers (kept for compatibility)
# ---------------------------------------------------------------------------


def _english_ratio(text: str) -> float:
    """英文字符占比（0.0-1.0）。"""
    meaningful_chars = [ch for ch in text if not ch.isspace()]
    if not meaningful_chars:
        return 0.0
    english_chars = len(LATIN_LETTER_PATTERN.findall(text))
    return min(1.0, english_chars / len(meaningful_chars))


def _noise_ratio(source_text: str, sanitize_report: SanitizeReport) -> float:
    """噪声比估算。"""
    if not source_text:
        return 0.0
    if sanitize_report.removed_segment_count == 0:
        return 0.0
    removed_proxy = min(len(source_text), sanitize_report.removed_segment_count * 12)
    return min(1.0, removed_proxy / max(len(source_text), 1))


# ---------------------------------------------------------------------------
# Fallback pipeline when spaCy is unavailable
# ---------------------------------------------------------------------------


def _run_pipeline_fallback(
    source_text: str,
    text: str,
    text_type: str,
    language_detected: str | None,
    english_ratio: float,
    all_actions_initial: list[str],
) -> _PipelineResult:
    """
    当 spaCy 不可用时的降级路径。
    强制走 regex 断句，action 记录为 "regex_sentence_split_no_spacy"（显式降级，可观测）。
    """
    logger.warning(
        "prepare_input: spaCy model unavailable, using regex fallback for sentence splitting"
    )
    all_actions = list(all_actions_initial)

    render_text, sanitize_report = sanitize_text(text)
    all_actions.extend(sanitize_report.actions)

    english_ratio = _english_ratio(render_text)
    noise_ratio = _noise_ratio(source_text, sanitize_report)

    # Force regex sentence split with explicit action (NOT generic "regex_sentence_split")
    paragraphs, sentences, a = layer5_split(
        render_text,
        fast_path=False,
        forced_regex_action="regex_sentence_split_no_spacy",
    )
    all_actions.extend(a)

    passes_quality, noise_ratio, a = layer6_quality_detection(
        english_ratio, noise_ratio, len(sentences), len(render_text)
    )
    all_actions.extend(a)

    remove_actions_count = sum(
        1
        for act in all_actions
        if "remove" in act or "strip" in act or "bs4" in act
    )
    final_report = SanitizeReport(
        actions=all_actions,
        removed_segment_count=max(
            sanitize_report.removed_segment_count, remove_actions_count
        ),
    )

    return _PipelineResult(
        render_text=render_text,
        paragraphs=paragraphs,
        sentences=sentences,
        sanitize_report=final_report,
        english_ratio=english_ratio,
        noise_ratio=noise_ratio,
        text_type=text_type,
        fast_path=False,
        language_detected=language_detected,
        fallback_reason="spacy_unavailable",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _run_pipeline(source_text: str) -> _PipelineResult:
    """内部 6 层 pipeline 编排。"""
    all_actions: list[str] = []

    # Layer 1
    text, a = layer1_preserve_source(source_text)
    all_actions.extend(a)

    # Layer 2
    text, a = layer2_minimal_fix(text)
    all_actions.extend(a)

    # Layer 3 + Layer 4 (structure detection is needed for fast-path decision)
    structure_hint, a = layer3_detect_structure(text)
    all_actions.extend(a)

    language_detected, english_ratio, a = layer4_detect_language(text, structure_hint)
    all_actions.extend(a)

    # Downgrade article_en if non-English detected
    text_type = structure_hint.text_type
    if text_type == "article_en" and language_detected not in ("en", None):
        text_type = "article_mixed"

    # spaCy availability check: run once, cache result, log if unavailable
    # This must happen BEFORE the fast-path decision so we know what's available
    if _spacy_available is None:
        _check_spacy_model()

    # If spaCy is unavailable, force fast_path=False (no point trying spaCy)
    if _spacy_available is False and structure_hint.text_type == "article_en":
        logger.info(
            "prepare_input: skip fast_path because spaCy is unavailable",
            extra={
                "text_type": text_type,
                "language_detected": language_detected,
                "english_ratio": round(english_ratio, 4),
            },
        )
        # Force to regex path; don't bother with fast-path check
        return _run_pipeline_fallback(
            source_text=source_text,
            text=text,
            text_type=text_type,
            language_detected=language_detected,
            english_ratio=english_ratio,
            all_actions_initial=all_actions,
        )

    # Fast-path decision
    fast_path, fallback_reason = _is_fast_path_eligible(
        structure_hint, english_ratio, text
    )
    logger.info(
        "prepare_input: fast_path decision",
        extra={
            "fast_path": fast_path,
            "fallback_reason": fallback_reason,
            "text_type": text_type,
            "language_detected": language_detected,
            "english_ratio": round(english_ratio, 4),
            "source_length": len(text),
        },
    )

    # Sanitize text (after structure/lang detection, before splitting)
    render_text, sanitize_report = sanitize_text(text)
    all_actions.extend(sanitize_report.actions)

    # Recalculate ratios after sanitization
    english_ratio = _english_ratio(render_text)
    noise_ratio = _noise_ratio(source_text, sanitize_report)

    # Layer 5: paragraph + sentence split
    paragraphs, sentences, a = layer5_split(render_text, fast_path)
    all_actions.extend(a)

    # Layer 6: quality detection
    passes_quality, noise_ratio, a = layer6_quality_detection(
        english_ratio, noise_ratio, len(sentences), len(render_text)
    )
    all_actions.extend(a)

    # Build final sanitize_report (aggregate all actions)
    remove_actions_count = sum(
        1
        for act in all_actions
        if "remove" in act or "strip" in act or "bs4" in act
    )

    final_report = SanitizeReport(
        actions=all_actions,
        removed_segment_count=max(sanitize_report.removed_segment_count, remove_actions_count),
    )

    logger.info(
        "prepare_input: pipeline completed",
        extra={
            "text_type": text_type,
            "fast_path": fast_path,
            "fallback_reason": fallback_reason,
            "language_detected": language_detected,
            "paragraph_count": len(paragraphs),
            "sentence_count": len(sentences),
            "actions": all_actions,
        },
    )

    return _PipelineResult(
        render_text=render_text,
        paragraphs=paragraphs,
        sentences=sentences,
        sanitize_report=final_report,
        english_ratio=english_ratio,
        noise_ratio=noise_ratio,
        text_type=text_type,
        fast_path=fast_path,
        language_detected=language_detected,
        fallback_reason=fallback_reason,
    )


def prepare_input(source_text: str) -> PreparedInput:
    """
    把原始输入转换成安全可渲染的正文结构，供主教学节点消费。

    原签名完全保留，内部实现替换为 6 层 pipeline：
      source_text -> layer1 -> layer2 -> layer3+layer4 -> sanitize -> layer5 -> layer6
    """
    result = _run_pipeline(source_text)

    return PreparedInput(
        source_text=source_text,
        render_text=result.render_text,
        paragraphs=result.paragraphs,
        sentences=result.sentences,
        sanitize_report=result.sanitize_report,
        english_ratio=result.english_ratio,
        noise_ratio=result.noise_ratio,
        text_type=result.text_type,
        fast_path=result.fast_path,
        language_detected=result.language_detected,
    )
