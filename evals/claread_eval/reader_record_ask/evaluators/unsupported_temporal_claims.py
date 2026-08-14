"""Dimension 3/11 — unsupported_temporal_claims.

Spec: extract year / date / relative-time tokens from ``final_text``;
each must be supported by ``allowed_temporal_claims`` (string-contains
match, e.g. ``"2026 年"`` passes when ``allowed=["2026"]``). When
``must_declare_no_year=True`` the answer must NOT contain year tokens
and MUST explicitly declare "article does not provide year" (or
equivalent). Failure ⇒ high severity.

This dimension is purely deterministic — "the model may know related
news" is never an excuse to let an unsupported year through.
"""

from __future__ import annotations

import re

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

DIMENSION = "unsupported_temporal_claims"

# Year token: 4-digit year (19xx / 20xx) not adjacent to other digits,
# optionally followed by whitespace + "年". Lookbehind/lookahead on
# digits only (not \b) so it works inside Chinese text where \b does
# not fire between a Han character and a digit (both are \w in Unicode
# mode).
YEAR_RE = re.compile(r"(?<![0-9])(?:19|20)\d{2}(?![0-9])\s*年?")

# ISO-style date 2026-01-02
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Chinese short date 1月2日 / 12月31日
CN_DATE_RE = re.compile(r"\d{1,2}月\d{1,2}日")

# Relative time words that imply a specific temporal anchor the article
# may not actually provide.
RELATIVE_TIME_WORDS: tuple[str, ...] = ("去年", "今年", "明年", "近日", "最近")

# Negation phrases that satisfy ``must_declare_no_year=True``.
NO_YEAR_DECLARATION_PHRASES: tuple[str, ...] = (
    "未提供",
    "未提及",
    "没有提到",
    "没有提供",
    "文章未",
    "未给出",
    "未说明",
)


def _token_allowed(token: str, allowed: list[str]) -> bool:
    """String-contains match: ``"2026 年"`` is allowed by ``"2026"``."""
    token_stripped = token.strip()
    for allowed_str in allowed:
        if not allowed_str:
            continue
        if allowed_str in token_stripped or token_stripped in allowed_str:
            return True
    return False


def evaluate_unsupported_temporal_claims(
    case: ReaderRecordAskCase,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    final_text = artifact.final_text or ""
    allowed = case.expected.allowed_temporal_claims

    unsupported: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        if token not in seen:
            seen.add(token)
            unsupported.append(token)

    for m in YEAR_RE.finditer(final_text):
        token = m.group(0).strip()
        if not _token_allowed(token, allowed):
            _add(token)

    for m in ISO_DATE_RE.finditer(final_text):
        token = m.group(0).strip()
        if not _token_allowed(token, allowed):
            _add(token)

    for m in CN_DATE_RE.finditer(final_text):
        token = m.group(0).strip()
        if not _token_allowed(token, allowed):
            _add(token)

    for word in RELATIVE_TIME_WORDS:
        if word in final_text and not _token_allowed(word, allowed):
            _add(word)

    reasons: list[str] = []
    if unsupported:
        reasons.append(f"unsupported temporal tokens: {unsupported}")

    if case.expected.must_declare_no_year:
        has_year = bool(YEAR_RE.search(final_text))
        if has_year:
            reasons.append(
                "must_declare_no_year=True but final_text contains year token"
            )
        has_declaration = any(p in final_text for p in NO_YEAR_DECLARATION_PHRASES)
        if not has_declaration:
            reasons.append(
                "must_declare_no_year=True but final_text lacks no-year "
                "declaration (e.g. 未提供/未提及/没有提到)"
            )

    passed = not reasons
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity="none" if passed else "high",
        details=(
            "unsupported_temporal_claims: all temporal tokens supported"
            if passed
            else "; ".join(reasons)
        ),
        evidence_refs=[],
    )
