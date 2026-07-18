"""Dimension 4/11 — numeric_grounding.

Spec: extract numeric tokens (plain integers, percentages, quantified
numbers with measure words) from ``final_text``; each must appear in
``allowed_numerics``. Excluded: years (handled by
``unsupported_temporal_claims``), ordinals inside ``第N题`` (handled by
``instruction_following``), and numbers that already appear in the
question (the answer is quoting the question, not inventing a number).
Failure ⇒ high severity.
"""

from __future__ import annotations

import re

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.evaluators.unsupported_temporal_claims import (
    YEAR_RE,
)
from claread_eval.reader_record_ask.schema import ReaderRecordAskR4A3Case

DIMENSION = "numeric_grounding"

# Ordinal marker 第N — the digit inside is an ordinal index, not a
# grounded numeric claim. Replaced with a placeholder before extraction.
ORDINAL_RE = re.compile(r"第\s*\d+\s*题")

# Percentages 30% / 12.5%
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")

# Quantified numbers with measure words: 858处 / 30 人 / 12.5 元
QUANTIFIED_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:元|美元|块|人|次|篇|题|个|起|处)"
)

# Plain integer (after masking years / ordinals / percentages / quantified)
PLAIN_INT_RE = re.compile(r"\d+")


def _bare_value(token: str) -> str:
    """Strip measure word / % / whitespace, return the bare numeric string."""
    m = re.match(r"\d+(?:\.\d+)?", token)
    return m.group(0) if m else token


def evaluate_numeric_grounding(
    case: ReaderRecordAskR4A3Case,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    final_text = artifact.final_text or ""
    allowed: set[str] = set(case.expected.allowed_numerics)

    # Numbers that appear in the question are quoted, not invented.
    for m in PLAIN_INT_RE.finditer(case.question):
        allowed.add(m.group(0))

    # Mask years and ordinals so their digits are not picked up as
    # standalone numeric claims.
    masked = YEAR_RE.sub(" ", final_text)
    masked = ORDINAL_RE.sub(" ", masked)

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
