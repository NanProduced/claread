"""Deterministic answer-correctness policy for Reader Record Ask.

This is a leaf module.  It deliberately knows nothing about the agent,
evidence registry, runtime dependencies, or Pydantic AI.  Callers provide only
the text that was visible to the model and receive deterministic policy data.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

ExplicitOutputKind = Literal["exercise_items", "none"]
ExplicitOutputConfidence = Literal["high", "indeterminate"]
PolicyViolationKind = Literal[
    "temporal_claim_unsupported",
    "explicit_count_mismatch",
    "unsupported_numeric",
    "language_consistency_violation",
    "geo_type_confusion",
]

_RAW_STRICT_ARTICLE_QUESTION_FORMS = (
    "这篇文章在讲什么",
    "这篇文章主要说了什么",
    "概括这篇文章的核心观点",
    "作者最想说明什么",
    "这篇文章是怎么展开论证的",
    "帮我出一道练习题",
    "基于这篇文章出一道小练习",
    "文章提到了哪些城市",
    "文章是什么时候发生/发布的",
    # Pure publish-date forms (event dates in the body are not publication dates).
    "这篇文章的发布日期是什么时候",
    "只用一句话概括文章",
    "文章没有提到的年份是什么？不得猜测",
    "基于文章出一道选择题，只允许一题",
)

# Exact-normalized pure publish/release-date questions.
# For these, only years with explicit publication semantics enter the
# temporal allowset — ordinary event / activity years do not.
_RAW_PUBLISH_DATE_QUESTION_FORMS = (
    "这篇文章的发布日期是什么时候",
)

# Exact-normalized "absent year / do not guess" questions: any year in
# the answer is unsupported (the correct answer states absence).
_RAW_ABSENT_YEAR_QUESTION_FORMS = (
    "文章没有提到的年份是什么？不得猜测",
)

_RAW_EXERCISE_QUESTION_FORMS = (
    "帮我出一道练习题",
    "基于这篇文章出一道小练习",
    "基于文章出一道选择题，只允许一题",
)

# Exact-normalized city-list forms. Region/province labels are not cities.
_RAW_CITY_LIST_QUESTION_FORMS = (
    "文章提到了哪些城市",
)

_TRAILING_QUESTION_PUNCTUATION_RE = re.compile(r"[。！？!?]+$")
_WHITESPACE_RE = re.compile(r"\s+")
_YEAR = r"(?:1[5-9]\d{2}|20\d{2})"

_TEMPORAL_PATTERNS = (
    re.compile(rf"(?<!\d)({_YEAR})\s*年"),
    re.compile(rf"(?<![\d.])({_YEAR})-(?:0?[1-9]|1[0-2])(?:-\d{{1,2}})?(?!\d)"),
    re.compile(
        rf"\b(?:January|February|March|April|May|June|July|August|"
        rf"September|October|November|December|Jan|Feb|Mar|Apr|Jun|"
        rf"Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+({_YEAR})\b",
        re.IGNORECASE,
    ),
    re.compile(rf"\b(?:in|since|by|from|during)\s+({_YEAR})\b", re.IGNORECASE),
    re.compile(rf"\bQ[1-4]\s+({_YEAR})\b", re.IGNORECASE),
    re.compile(rf"\b({_YEAR})\s+Q[1-4]\b", re.IGNORECASE),
)
_YEAR_RANGE_RE = re.compile(
    rf"(?<!\d)({_YEAR})\s*[-–—]\s*({_YEAR})\s*"
    rf"(?:年|学年|academic\s+year)",
    re.IGNORECASE,
)

# Publication-semantic year extraction for pure publish-date questions.
# Year must co-occur with an explicit publication/release collocation.
# Deliberately excludes bare event years and weak "发布了/发布会" senses.
_PUBLICATION_YEAR_PATTERNS = (
    # 发表于2024 / 发布于 2024 年 / 刊登于2024年5月
    re.compile(
        rf"(?:发表于|刊登于|发布于|出版于|首发于|刊发于)\s*"
        rf"(?:(?:1[5-9]\d{{2}}|20\d{{2}})\s*年\s*)?"
        rf"(?:0?\d{{1,2}}\s*月\s*)?"
        rf"(?:0?\d{{1,2}}\s*日\s*)?"
        rf"({_YEAR})",
    ),
    re.compile(
        rf"(?:发表于|刊登于|发布于|出版于|首发于|刊发于)\s*"
        rf"({_YEAR})\s*年?",
    ),
    # 发布日期：2024 / 发表日期 2024年
    re.compile(
        rf"(?:发布日期|发表日期|出版日期|发布时间|发表时间|出版时间)"
        rf"\s*[:：]?\s*({_YEAR})",
    ),
    # 2024年发布于 / 2024 年正式发表于
    re.compile(
        rf"({_YEAR})\s*年\s*(?:正式)?"
        rf"(?:发表|刊登|出版|发布|首发|刊发)于",
    ),
    # 2024年发表 / 2024年出版 (not 发布了 / 发布会 — require 发表|刊登|出版
    # or 发布于 already covered above; bare 发布 alone is too ambiguous)
    re.compile(
        rf"({_YEAR})\s*年\s*(?:正式)?(?:发表|刊登|出版|首发|刊发)"
        rf"(?!会|预警|通知|声明|新闻)",
    ),
    # English: published on/in … 2024; first published 2024
    re.compile(
        rf"\b(?:first\s+)?published\s+(?:on|in)\s+"
        rf"(?:[A-Za-z0-9,]+\s+){{0,5}}({_YEAR})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:first\s+)?published\s+({_YEAR})\b",
        re.IGNORECASE,
    ),
    # released on/in as media release (paired with article/paper/story/report)
    re.compile(
        rf"\b(?:article|paper|story|report|piece)\s+"
        rf"(?:was\s+)?released\s+(?:on|in)\s+"
        rf"(?:[A-Za-z0-9,]+\s+){{0,5}}({_YEAR})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\breleased\s+(?:on|in)\s+"
        rf"(?:[A-Za-z0-9,]+\s+){{0,5}}({_YEAR})\b"
        rf"(?=[^.]{{0,40}}\b(?:article|paper|story|report|edition)\b)",
        re.IGNORECASE,
    ),
)

_INDETERMINATE_EXERCISE_PHRASES = ("几道题", "若干题", "一组题")
_COUNTED_EXERCISE_PATTERNS = (
    (1, re.compile(r"一道(?:练习题|小练习|选择题|题)|(?<!第)一题|只允许一题|只要一题|1道题")),
    (2, re.compile(r"两道(?:练习题|题)|(?<!第)两题|2道题")),
    (3, re.compile(r"三道(?:练习题|题)|(?<!第)三题|3道题")),
)

_ANSWER_SECTION_RE = re.compile(
    r"^(?:参考答案|答案|解析|Answer|Explanation)\s*[:：]",
    re.IGNORECASE,
)
_TOP_LEVEL_ITEM_PATTERNS = (
    re.compile(r"^\d+[.、)]\s+\S"),
    re.compile(r"^Q\d+[.)、]?\s*\S", re.IGNORECASE),
    re.compile(r"^第\d+题[.、:：)]?\s*\S"),
)

# Structural / non-claim numbers when scanning exercise drafts.
_LIST_MARKER_RE = re.compile(r"(?m)^[ \t]*\d+[ \t]*[.、)](?!\d)")
_ORDINAL_ITEM_RE = re.compile(r"第\s*\d+\s*题")
_CN_DATE_COMPONENT_RE = re.compile(r"\d{1,2}\s*[月日]")
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")
_PLAIN_INT_RE = re.compile(r"\d+")

_SENTENCE_SPLIT_RE = re.compile(r"[。！？.!?]+")
_ENGLISH_RATIO_THRESHOLD = 0.7

_TEMPORAL_RETRY_MESSAGE = (
    "The complete article context does not contain that date. Remove the "
    "unsupported date, or state that the article does not provide it "
    "without repeating a specific date."
)
_NUMERIC_RETRY_MESSAGE = (
    "The answer introduces a number that is not present in the supplied "
    "article context. Remove invented statistics or quantities, or ground "
    "them only in numbers that appear in the article text."
)
_LANGUAGE_RETRY_MESSAGE = (
    "The user asked in Chinese for a practice item. Write the exercise stem "
    "and explanation in Chinese; do not use whole English sentences."
)
_GEO_TYPE_RETRY_MESSAGE = (
    "The user asked for cities only. Remove provinces, states, regions, "
    "autonomous regions, or counties from the city list; list cities only."
)
_ARTICLE_ONLY_PROMPT = (
    "Answer using only facts explicitly supported by the supplied article "
    "context. Do not add external knowledge, inferred background, or "
    "unmentioned details. If the article does not provide the requested "
    "information, say clearly that the article does not provide it "
    "(「文章未提供」) without inventing substitutes."
)
_CITY_LIST_PROMPT = (
    "The user asked which cities are mentioned. List cities only — do not "
    "treat provinces, states, regions, autonomous regions, counties, or "
    "districts as cities."
)
_NUMERIC_PROMPT = (
    "Do not invent statistics, counts, percentages, or measurements that "
    "do not appear in the supplied article context. List markers "
    "(1. / 2、) are not factual quantities."
)
_CHINESE_ANSWER_PROMPT = (
    "The user asked in Chinese. Answer in Chinese. Keep proper nouns, "
    "short quoted terms, and necessary technical abbreviations; do not "
    "write whole English sentences."
)

# High-confidence non-city geo markers for city-list answers only.
# Avoid bare 「区」 (too many false positives: 社区 / 区域).
_CN_NON_CITY_GEO_RE = re.compile(
    r"(?:省|自治区|地区|(?<![市县])州|县)"
)
_EN_NON_CITY_GEO_RE = re.compile(
    r"\b(?:states?|provinces?|counties|county|regions?|districts?)\b",
    re.IGNORECASE,
)

# Publish-date answers that already correctly refuse without naming a year.
_PUBLISH_DATE_ABSENT_PHRASE_RE = re.compile(
    r"(?:未提供|未提及|没有提供|没有提到|未说明|未给出|不提供)"
    r".{0,12}(?:发布|发表|出版)?(?:日期|时间)?"
    r"|(?:发布|发表|出版)(?:日期|时间).{0,12}(?:未提供|未提及|没有|未知|不明)"
)


def _normalize_question(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return _TRAILING_QUESTION_PUNCTUATION_RE.sub("", normalized).strip()


STRICT_ARTICLE_QUESTION_FORMS = frozenset(
    _normalize_question(value) for value in _RAW_STRICT_ARTICLE_QUESTION_FORMS
)

PUBLISH_DATE_QUESTION_FORMS = frozenset(
    _normalize_question(value) for value in _RAW_PUBLISH_DATE_QUESTION_FORMS
)

ABSENT_YEAR_QUESTION_FORMS = frozenset(
    _normalize_question(value) for value in _RAW_ABSENT_YEAR_QUESTION_FORMS
)

EXERCISE_QUESTION_FORMS = frozenset(
    _normalize_question(value) for value in _RAW_EXERCISE_QUESTION_FORMS
)

CITY_LIST_QUESTION_FORMS = frozenset(
    _normalize_question(value) for value in _RAW_CITY_LIST_QUESTION_FORMS
)


@dataclass(frozen=True, slots=True)
class ExplicitOutputConstraint:
    kind: ExplicitOutputKind
    requested_count: int | None
    extraction_confidence: ExplicitOutputConfidence


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    kind: PolicyViolationKind
    detail: str


@dataclass(frozen=True, slots=True)
class AnswerCorrectnessPolicy:
    """Public surface: temporal allowset, explicit count, strict/complete flags.

    Numeric allowset and question-type routing are private implementation
    details consumed only by ``render_prompt_block`` / ``evaluate_draft``.
    """

    temporal_allowset: frozenset[str]
    explicit_output: ExplicitOutputConstraint
    is_article_only_strict: bool
    baseline_is_complete: bool
    # Private implementation — not part of the stable public contract.
    _numeric_allowset: frozenset[str]
    _is_publish_date_question: bool
    _is_absent_year_question: bool
    _is_exercise_question: bool
    _is_city_list_question: bool
    _user_message_is_chinese: bool

    def render_prompt_block(self) -> str:
        instructions = [
            "Follow the requested answer format exactly.",
            "Do not add facts that the supplied article context does not support.",
        ]
        if self.baseline_is_complete and self.is_article_only_strict:
            instructions.append(_ARTICLE_ONLY_PROMPT)
            if self._user_message_is_chinese:
                instructions.append(_CHINESE_ANSWER_PROMPT)
            instructions.append(_NUMERIC_PROMPT)

            if self._is_city_list_question:
                instructions.append(_CITY_LIST_PROMPT)

            if self._is_publish_date_question:
                if self.temporal_allowset:
                    years = ", ".join(sorted(self.temporal_allowset))
                    instructions.append(
                        "The question asks for a publication/release date. "
                        f"The article states these publication years: {years}. "
                        "Do not use any other year. Event or activity dates "
                        "are not publication dates."
                    )
                else:
                    instructions.append(
                        "The question asks for a publication/release date. "
                        "The article does not state an explicit publication/"
                        "release date (event or activity years do not count). "
                        "Reply briefly that the article does not provide a "
                        "publication date (「文章未提供发布日期」) and do not "
                        "name any year. Do not invent event dates as publish dates."
                    )
            elif self._is_absent_year_question:
                instructions.append(
                    "The user asks for a year the article does not mention and "
                    "forbids guessing. Do not invent or name any year. State "
                    "that the requested year is not provided / must not be guessed."
                )
            elif self.temporal_allowset:
                years = ", ".join(sorted(self.temporal_allowset))
                instructions.append(
                    f"The complete article context contains these specific years: {years}. "
                    "Do not output any other specific year."
                )
            else:
                instructions.append(
                    "The complete article context provides no specific year or date; "
                    "do not invent one."
                )

            if self._is_exercise_question:
                instructions.append(
                    "Write the practice item in Chinese when the user asked in Chinese. "
                    "Do not use whole English sentences for the stem or explanation."
                )
                # Prompt mitigation only — not a hard tool-call gate.
                instructions.append(
                    "Baseline coverage is complete; answer from the baseline "
                    "without calling read_range or search_current_article."
                )

        if (
            self.explicit_output.kind == "exercise_items"
            and self.explicit_output.extraction_confidence == "high"
            and self.explicit_output.requested_count is not None
        ):
            instructions.append(
                f"Return exactly {self.explicit_output.requested_count} exercise item(s)."
            )
        return (
            "<answer_correctness>\n"
            + "\n".join(f"- {instruction}" for instruction in instructions)
            + "\n</answer_correctness>"
        )

    def evaluate_draft(
        self,
        *,
        draft_answer_text: str,
    ) -> tuple[PolicyViolation, ...]:
        violations: list[PolicyViolation] = []
        if self.baseline_is_complete and self.is_article_only_strict:
            # Publish-date: if the draft already correctly refuses without a
            # year token, do not thrash retries on residual heuristics.
            publish_date_clean_absent = (
                self._is_publish_date_question
                and not _extract_temporal_years(draft_answer_text)
                and bool(_PUBLISH_DATE_ABSENT_PHRASE_RE.search(draft_answer_text))
            )

            unsupported = _extract_temporal_years(draft_answer_text).difference(
                self.temporal_allowset
            )
            if unsupported and not publish_date_clean_absent:
                detail = _TEMPORAL_RETRY_MESSAGE
                if self._is_publish_date_question and not self.temporal_allowset:
                    detail = (
                        "Reply that the article does not provide a publication "
                        "date without naming any year (「文章未提供发布日期」)."
                    )
                violations.append(
                    PolicyViolation(
                        kind="temporal_claim_unsupported",
                        detail=detail,
                    )
                )

            # High-confidence numeric invention check for all strict
            # article-only turns (not only exercises). List markers /
            # ordinals / date parts are excluded by the extractor.
            if not publish_date_clean_absent:
                unsupported_nums = _extract_claim_numerics(
                    draft_answer_text
                ).difference(self._numeric_allowset)
                unsupported_nums = frozenset(
                    n
                    for n in unsupported_nums
                    if not re.fullmatch(_YEAR, n)
                )
                if unsupported_nums:
                    violations.append(
                        PolicyViolation(
                            kind="unsupported_numeric",
                            detail=_NUMERIC_RETRY_MESSAGE,
                        )
                    )

            if self._is_city_list_question and _has_non_city_geo_label(
                draft_answer_text
            ):
                violations.append(
                    PolicyViolation(
                        kind="geo_type_confusion",
                        detail=_GEO_TYPE_RETRY_MESSAGE,
                    )
                )

            if self._is_exercise_question and _has_whole_sentence_english(
                draft_answer_text
            ):
                violations.append(
                    PolicyViolation(
                        kind="language_consistency_violation",
                        detail=_LANGUAGE_RETRY_MESSAGE,
                    )
                )

        constraint = self.explicit_output
        if (
            constraint.kind == "exercise_items"
            and constraint.extraction_confidence == "high"
            and constraint.requested_count is not None
        ):
            actual_count = _parse_answer_exercise_count(draft_answer_text)
            if actual_count is not None and actual_count != constraint.requested_count:
                violations.append(
                    PolicyViolation(
                        kind="explicit_count_mismatch",
                        detail=(
                            "The answer must contain exactly "
                            f"{constraint.requested_count} exercise item(s), but contains "
                            f"{actual_count}."
                        ),
                    )
                )

        return tuple(sorted(violations, key=lambda violation: violation.kind))


def _extract_temporal_years(text: str) -> frozenset[str]:
    years: set[str] = set()
    for match in _YEAR_RANGE_RE.finditer(text):
        years.update(match.groups())
    for pattern in _TEMPORAL_PATTERNS:
        years.update(match.group(1) for match in pattern.finditer(text))
    return frozenset(years)


def _extract_publication_years(text: str) -> frozenset[str]:
    """Years that co-occur with explicit publication/release semantics.

    Ordinary event / activity years do not qualify. Weak senses such as
    ``发布了``, ``发布会``, or bare ``2024年…举办`` are excluded by
    requiring strong collocations (发表于 / 发布于 / published on / …).
    """
    years: set[str] = set()
    for pattern in _PUBLICATION_YEAR_PATTERNS:
        years.update(match.group(1) for match in pattern.finditer(text))
    return frozenset(years)


def _extract_claim_numerics(text: str) -> frozenset[str]:
    """Bare numeric claim tokens (not list markers / ordinals / date parts)."""
    masked = _LIST_MARKER_RE.sub(" ", text)
    masked = _ORDINAL_ITEM_RE.sub(" ", masked)
    masked = _YEAR_RANGE_RE.sub(" ", masked)
    for pattern in _TEMPORAL_PATTERNS:
        masked = pattern.sub(" ", masked)
    masked = _CN_DATE_COMPONENT_RE.sub(" ", masked)
    values: set[str] = set()
    for match in _PERCENT_RE.finditer(masked):
        values.add(match.group(0).replace(" ", ""))
        masked = masked[: match.start()] + " " + masked[match.end() :]
    for match in _PLAIN_INT_RE.finditer(masked):
        values.add(match.group(0))
    return frozenset(values)


def _english_ratio(text: str) -> float:
    if not text:
        return 0.0
    alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    total = sum(1 for c in text if not c.isspace())
    return alpha / total if total else 0.0


def _has_whole_sentence_english(text: str) -> bool:
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if len(sentence) < 12:
            continue
        if _english_ratio(sentence) > _ENGLISH_RATIO_THRESHOLD:
            return True
    return False


def _has_non_city_geo_label(text: str) -> bool:
    """High-confidence region/province/county markers (not bare 区)."""
    if _CN_NON_CITY_GEO_RE.search(text):
        return True
    if _EN_NON_CITY_GEO_RE.search(text):
        return True
    return False


def _user_message_is_chinese(user_message: str) -> bool:
    """True when the question is primarily CJK (high-confidence)."""
    if not user_message or not user_message.strip():
        return False
    cjk = sum(1 for c in user_message if "\u4e00" <= c <= "\u9fff")
    letters = sum(1 for c in user_message if c.isalpha())
    if cjk == 0:
        return False
    # Prefer Chinese when CJK dominates alphabetic content.
    return cjk >= max(2, letters)


def _extract_explicit_output(user_message: str) -> ExplicitOutputConstraint:
    has_indeterminate = any(phrase in user_message for phrase in _INDETERMINATE_EXERCISE_PHRASES)
    requested_counts = {
        count for count, pattern in _COUNTED_EXERCISE_PATTERNS if pattern.search(user_message)
    }
    if has_indeterminate or len(requested_counts) > 1:
        return ExplicitOutputConstraint("exercise_items", None, "indeterminate")
    if requested_counts:
        return ExplicitOutputConstraint("exercise_items", next(iter(requested_counts)), "high")
    return ExplicitOutputConstraint("none", None, "indeterminate")


def _parse_answer_exercise_count(draft_answer_text: str) -> int | None:
    question_lines: list[str] = []
    for line in draft_answer_text.splitlines():
        if _ANSWER_SECTION_RE.match(line):
            break
        question_lines.append(line)

    marker_count = sum(
        1
        for line in question_lines
        if any(pattern.match(line) for pattern in _TOP_LEVEL_ITEM_PATTERNS)
    )
    if marker_count:
        return marker_count

    question_marks = sum(line.count("?") + line.count("？") for line in question_lines)
    if question_marks == 1:
        return 1
    return None


def build_answer_correctness_policy(
    *,
    user_message: str,
    model_visible_chunk_texts: tuple[str, ...],
    baseline_is_complete: bool,
) -> AnswerCorrectnessPolicy:
    normalized = _normalize_question(user_message)
    is_strict = normalized in STRICT_ARTICLE_QUESTION_FORMS
    is_publish_date = normalized in PUBLISH_DATE_QUESTION_FORMS
    is_absent_year = normalized in ABSENT_YEAR_QUESTION_FORMS
    is_exercise = normalized in EXERCISE_QUESTION_FORMS
    is_city_list = normalized in CITY_LIST_QUESTION_FORMS

    temporal_allowset: set[str] = set()
    numeric_allowset: set[str] = set()

    if is_absent_year:
        # Any named year is a guess for this question form.
        temporal_allowset = set()
    elif is_publish_date:
        # Only publication-semantic years — not event/activity years.
        for chunk_text in model_visible_chunk_texts:
            temporal_allowset.update(_extract_publication_years(chunk_text))
    else:
        for chunk_text in model_visible_chunk_texts:
            temporal_allowset.update(_extract_temporal_years(chunk_text))

    for chunk_text in model_visible_chunk_texts:
        numeric_allowset.update(_extract_claim_numerics(chunk_text))

    return AnswerCorrectnessPolicy(
        temporal_allowset=frozenset(temporal_allowset),
        explicit_output=_extract_explicit_output(user_message),
        is_article_only_strict=is_strict,
        baseline_is_complete=baseline_is_complete,
        _numeric_allowset=frozenset(numeric_allowset),
        _is_publish_date_question=is_publish_date,
        _is_absent_year_question=is_absent_year,
        _is_exercise_question=is_exercise,
        _is_city_list_question=is_city_list,
        _user_message_is_chinese=_user_message_is_chinese(user_message),
    )
