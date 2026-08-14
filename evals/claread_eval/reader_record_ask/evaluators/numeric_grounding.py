"""Dimension 4/11 — numeric_grounding.

Spec: extract numeric tokens (plain integers, percentages, quantified
numbers with measure words) from ``final_text``; each must appear in
``allowed_numerics``. Excluded:

- years (handled by ``unsupported_temporal_claims``)
- ordinals inside ``第N题`` (handled by ``instruction_following``)
- top-level list markers ``1.`` / ``2、`` / ``3)`` (structural, not factual)
- CN date components ``M月`` / ``D日`` (handled by
  ``unsupported_temporal_claims``)
- numbers that already appear in the question (the answer is quoting
  the question, not inventing a number)

Failure ⇒ high severity.

Audit findings addressed here:

1. Structural numbering (``1.`` / ``2.`` / ``3.`` from exercise answer
   lists and reference answer lists) was being picked up by
   ``PLAIN_INT_RE`` as a numeric claim → false positive. Now masked.
2. CN date components (``M月``, ``D日``) left behind after
   ``YEAR_RE`` strips ``YYYY年`` were picked up as standalone
   integers → false positive (the underlying year hallucination is
   still caught by ``unsupported_temporal_claims``). Now masked.
3. Article-actual numbers missing from ``allowed_numerics`` (e.g.
   ``800`` from "More than 800 wildfires") — this is a dataset contract
   fix, addressed in the case files, not here.
"""

from __future__ import annotations

import re

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.evaluators.unsupported_temporal_claims import (
    YEAR_RE,
)
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

DIMENSION = "numeric_grounding"

# Ordinal marker 第N — the digit inside is an ordinal index, not a
# grounded numeric claim. Replaced with a placeholder before extraction.
ORDINAL_RE = re.compile(r"第\s*\d+\s*题")

# Top-level structural list markers
# ``1.`` / ``2、`` / ``3)`` at the start of a line are structural
# numbering (exercise items, reference-answer items, enumerated
# answers). The digit inside is NOT a numeric claim about the article.
# The negative lookahead ``(?!\d)`` ensures we do NOT mask decimals
# such as ``1.5`` (the ``5`` after ``1.`` blocks the match) while
# still masking ``1.文章`` / ``1、文章`` / ``1) foo`` where a
# non-digit (including CJK characters) immediately follows the marker.
LIST_MARKER_RE = re.compile(r"(?m)^[ \t]*\d+[ \t]*[.、)](?!\d)")

# CN date components ``M月`` and ``D日``. After
# ``YEAR_RE`` strips ``YYYY年``, leftover ``6月`` / ``5日`` fragments
# are date structure, not standalone numeric claims. They are still
# caught by ``unsupported_temporal_claims`` via ``CN_DATE_RE`` /
# ``ISO_DATE_RE`` / ``YEAR_RE`` when the model hallucinates a date.
CN_DATE_COMPONENT_RE = re.compile(r"\d{1,2}\s*[月日]")

# Percentages 30% / 12.5%
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")

# Quantified numbers with measure words: 858处 / 30 人 / 12.5 元
QUANTIFIED_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:元|美元|块|人|次|篇|题|个|起|处)"
)

# Plain integer (after masking years / ordinals / list markers / CN date
# components / percentages / quantified)
PLAIN_INT_RE = re.compile(r"\d+")


def _bare_value(token: str) -> str:
    """Strip measure word / % / whitespace, return the bare numeric string."""
    m = re.match(r"\d+(?:\.\d+)?", token)
    return m.group(0) if m else token


def evaluate_numeric_grounding(
    case: ReaderRecordAskCase,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    final_text = artifact.final_text or ""
    allowed: set[str] = set(case.expected.allowed_numerics)

    # Numbers that appear in the question are quoted, not invented.
    for m in PLAIN_INT_RE.finditer(case.question):
        allowed.add(m.group(0))

    # Mask years and ordinals so their digits are not picked up as
    # standalone numeric claims. Temporal tokens (years, ISO dates, CN
    # dates) are owned by ``unsupported_temporal_claims``.
    masked = YEAR_RE.sub(" ", final_text)
    masked = ORDINAL_RE.sub(" ", masked)

    # Mask structural list markers and CN date
    # components so their digits are not re-counted as numeric claims.
    masked = LIST_MARKER_RE.sub(" ", masked)
    masked = CN_DATE_COMPONENT_RE.sub(" ", masked)

    unsupported: list[str] = []
    seen: set[str] = set()

    def _check(token: str) -> None:
        bare = _bare_value(token)
        if bare in allowed or token in allowed:
            return
        if bare not in seen:
            seen.add(bare)
            unsupported.append(token)

    # Percentages first (so their digits are not re-counted as plain ints)
    for m in PERCENT_RE.finditer(masked):
        _check(m.group(0))
    masked = PERCENT_RE.sub(" ", masked)

    # Quantified numbers next
    for m in QUANTIFIED_RE.finditer(masked):
        _check(m.group(0))
    masked = QUANTIFIED_RE.sub(" ", masked)

    # Remaining plain integers
    for m in PLAIN_INT_RE.finditer(masked):
        _check(m.group(0))

    passed = not unsupported
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity="none" if passed else "high",
        details=(
            "numeric_grounding: all numeric tokens grounded"
            if passed
            else f"unsupported numeric tokens: {unsupported}"
        ),
        evidence_refs=[],
    )
