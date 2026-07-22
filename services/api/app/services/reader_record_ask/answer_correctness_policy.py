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

# Exact-normalized publish/release-date questions. For these, event years
# visible in the article must NOT authorize a specific year in the answer —
# a publication/release date is a distinct claim from an in-article event date.
_RAW_PUBLISH_DATE_QUESTION_FORMS = (
    "这篇文章的发布日期是什么时候",
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

_TEMPORAL_RETRY_MESSAGE = (
    "The complete article context does not contain that date. Remove the "
    "unsupported date, or state that the article does not provide it "
    "without repeating a specific date."
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
    temporal_allowset: frozenset[str]
    explicit_output: ExplicitOutputConstraint
    is_article_only_strict: bool
    baseline_is_complete: bool

    def render_prompt_block(self) -> str:
        instructions = [
            "Follow the requested answer format exactly.",
            "Do not add facts that the supplied article context does not support.",
        ]
        if self.baseline_is_complete and self.is_article_only_strict:
            if self.temporal_allowset:
                years = ", ".join(sorted(self.temporal_allowset))
                instructions.append(
                    f"The complete article context contains these specific years: {years}. "
                    "Do not output any other specific year."
                )
            else:
                instructions.append(
                    "The complete article context provides no specific year or date "
                    "that answers this question; do not invent one. "
                    "If the question asks for a publication/release date, event dates "
                    "in the article are not publication dates — say the article does "
                    "not provide a publication date without naming a year."
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
            unsupported = _extract_temporal_years(draft_answer_text).difference(
                self.temporal_allowset
            )
            if unsupported:
                violations.append(
                    PolicyViolation(
                        kind="temporal_claim_unsupported",
                        detail=_TEMPORAL_RETRY_MESSAGE,
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

    temporal_allowset: set[str] = set()
    # Publish/release-date questions: in-article event years must not authorize
    # a year token in the answer (event date ≠ publication date). Keep allowset
    # empty so any specific year is a temporal_claim_unsupported violation when
    # baseline is complete and the question is strict.
    if not is_publish_date:
        for chunk_text in model_visible_chunk_texts:
            temporal_allowset.update(_extract_temporal_years(chunk_text))

    return AnswerCorrectnessPolicy(
        temporal_allowset=frozenset(temporal_allowset),
        explicit_output=_extract_explicit_output(user_message),
        is_article_only_strict=is_strict,
        baseline_is_complete=baseline_is_complete,
    )
