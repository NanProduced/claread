"""Dimension 7/11 — instruction_following.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
spec.md` — Requirement: P1-4 instruction count effectiveness.

Previous implementation only counted explicit numbered markers (Q1,
第N题, 1./2./3.). Several gaps:

1. Single unnumbered question (e.g. "文章主旨是什么？") → counted as 0
   because no numbered markers match. Should count as 1.
2. One question with A/B/C/D options → the options were not recognized
   as a single exercise. Should count as 1, not 5.
3. Decimals like "1.5" at line start → falsely matched LIST_ITEM_RE
   as list item "1". Should NOT match.
4. When count cannot be reliably determined (no markers, no
   interrogative punctuation), the evaluator silently PASSed. Should
   emit ``indeterminate`` and FAIL (never silently PASS).

New contract:

- ``requested_count_kind="exercise_items"``: count distinct exercise
  items via numbered markers (Q1/第N题/1.) and, as fallback, multiple-
  choice options (A./B./C./D.) or a single interrogative sentence.
  Multiple interrogatives → indeterminate. No markers → indeterminate.
- ``requested_count_kind="sentences"``: split by Chinese 。！？ and
  ASCII .!? (NOT between two alphanumeric chars, to avoid splitting
  decimals like "1.5" and abbreviations like "e.g.").
- ``requested_count_kind="none"``: always pass.
- When count is ``indeterminate`` and a constraint exists → FAIL with
  ``severity="medium"`` and details containing ``indeterminate``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskR4A3Case

DIMENSION = "instruction_following"

# Numbered exercise markers
Q_MARK_RE = re.compile(r"Q\s*(\d+)", re.IGNORECASE)
ORDINAL_TOPIC_RE = re.compile(r"第\s*(\d+)\s*题")
# Numbered list items at line start: 1. / 2、 / 3) — but NOT decimals
# like "1.5" (negative lookahead on digit after the punctuation).
LIST_ITEM_RE = re.compile(r"(?:^|\n)\s*(\d+)\s*[.、)](?!\d)")

# Multiple-choice option markers: A. / B、 / C) / D) at line start.
# Used as a fallback signal: if numbered markers are absent but
# multiple-choice options are present, the answer is one exercise with
# options (count=1).
MULTIPLE_CHOICE_RE = re.compile(r"(?:^|\n)\s*[A-Da-d]\s*[.、)]")

# Interrogative punctuation (Chinese ？ and ASCII ?)
INTERROGATIVE_RE = re.compile(r"[？?]")

# Sentence boundary: Chinese 。！？ always; ASCII .!? only when NOT
# between two alphanumeric characters (avoids splitting decimals like
# "1.5" and abbreviations like "e.g.").
#
# The pattern matches a sequence of sentence-ending punctuation where:
# - Chinese 。！？ always counts as a boundary.
# - ASCII .!? counts as a boundary when NOT preceded by alphanumeric
#   OR NOT followed by alphanumeric (i.e., not between two alnum chars).
SENTENCE_BOUNDARY_RE = re.compile(
    r"[。！？]+|(?<![a-zA-Z0-9])[.!?]+|[.!?]+(?![a-zA-Z0-9])"
)


@dataclass(frozen=True)
class _CountResult:
    """Structured result of counting exercise items or sentences.

    ``count is None`` means ``indeterminate`` — the evaluator cannot
    reliably determine the count from the text. In that case, if a
    count constraint exists, the dimension FAILs (never silently PASS).
    """

    count: int | None
    reason: str


def _count_exercise_items(text: str) -> _CountResult:
    """Count distinct exercise items in ``text``.

    Numbered markers (Q1, 第N题, 1.) are de-duplicated by taking the
    max across signals — ``第1题`` and ``1.`` referring to the same
    item are NOT double-counted.

    When no numbered markers are present, fall back to:
    1. Multiple-choice options (A./B./C./D.) → 1 item (one question
       with options).
    2. Single interrogative sentence (one ？/?) → 1 item.
    3. Multiple interrogatives → indeterminate (could be one multi-
       part question or multiple exercises).
    4. No markers and no interrogatives → indeterminate.
    """
    signals: list[int] = []

    q_matches = {m.group(1) for m in Q_MARK_RE.finditer(text)}
    if q_matches:
        signals.append(len(q_matches))

    ordinal_matches = {m.group(1) for m in ORDINAL_TOPIC_RE.finditer(text)}
    if ordinal_matches:
        signals.append(len(ordinal_matches))

    list_matches = LIST_ITEM_RE.findall(text)
    if list_matches:
        # Distinct list indices (deduplicate "1." appearing multiple times)
        signals.append(len(set(list_matches)))

    if signals:
        return _CountResult(
            count=max(signals),
            reason=f"numbered markers detected (signals={signals})",
        )

    # No numbered markers — try multiple-choice options.
    if MULTIPLE_CHOICE_RE.search(text):
        return _CountResult(
            count=1,
            reason="multiple-choice options detected (one question with options)",
        )

    # Try interrogative sentences.
    interrogatives = INTERROGATIVE_RE.findall(text)
    if len(interrogatives) == 1:
        return _CountResult(
            count=1,
            reason="single unnumbered interrogative sentence",
        )
    if len(interrogatives) > 1:
        return _CountResult(
            count=None,  # indeterminate
            reason=(
                f"{len(interrogatives)} interrogative markers; cannot "
                "distinguish multi-part question from multiple exercises"
            ),
        )

    # No markers at all — cannot reliably determine.
    return _CountResult(
        count=None,  # indeterminate
        reason=(
            "no exercise markers and no interrogative punctuation; "
            "cannot reliably determine count"
        ),
    )


def _count_sentences(text: str) -> _CountResult:
    """Count sentences by splitting on sentence-ending punctuation.

    Handles:
    - Chinese 。！？
    - ASCII .!? — but NOT between two alphanumeric chars (avoids
      splitting decimals like "1.5" and abbreviations like "e.g.").

    Empty text → 0 sentences. Text with no boundaries → 1 sentence
    (treat the whole text as one sentence without ending punctuation).
    """
    if not text.strip():
        return _CountResult(count=0, reason="empty text")

    parts = SENTENCE_BOUNDARY_RE.split(text)
    non_empty = [p.strip() for p in parts if p.strip()]

    if not non_empty:
        # Text has no sentence boundaries — treat as 1 sentence.
        return _CountResult(
            count=1,
            reason="no sentence boundaries; treating as 1 sentence",
        )

    return _CountResult(
        count=len(non_empty),
        reason=f"split into {len(non_empty)} sentences",
    )


def evaluate_instruction_following(
    case: ReaderRecordAskR4A3Case,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    """Evaluate whether the answer satisfies the requested count constraint.

    See module docstring for the full contract. When the count is
    ``indeterminate`` and a constraint exists, the dimension FAILs
    with ``severity="medium"`` and details containing ``indeterminate``.
    """
    final_text = artifact.final_text or ""
    kind = case.expected.requested_count_kind
    requested = case.expected.requested_count

    if kind == "none":
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=True,
            severity="none",
            details="instruction_following: requested_count_kind=none, skip",
        )

    if requested is None:
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=True,
            severity="none",
            details=(
                "instruction_following: requested_count is None, "
                "no count constraint"
            ),
        )

    if kind == "exercise_items":
        result = _count_exercise_items(final_text)
        if result.count is None:
            # Indeterminate — cannot silently PASS.
            return EvalDimensionResult(
                dimension=DIMENSION,
                passed=False,
                severity="medium",
                details=(
                    f"instruction_following: exercise_items indeterminate; "
                    f"reason={result.reason}; requested={requested}"
                ),
            )
        passed = result.count == requested
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=passed,
            severity="none" if passed else "high",
            details=(
                f"instruction_following: exercise_items actual={result.count} "
                f"requested={requested}; {result.reason}"
            ),
        )

    if kind == "sentences":
        result = _count_sentences(final_text)
        if result.count is None:
            return EvalDimensionResult(
                dimension=DIMENSION,
                passed=False,
                severity="medium",
                details=(
                    f"instruction_following: sentences indeterminate; "
                    f"reason={result.reason}; requested<={requested}"
                ),
            )
        # "一句话" → ≤ requested sentences acceptable; >requested ⇒ failure.
        passed = result.count <= requested
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=passed,
            severity="none" if passed else "high",
            details=(
                f"instruction_following: sentences actual={result.count} "
                f"requested<={requested}; {result.reason}"
            ),
        )

    # Unknown kind — fail safe.
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=False,
        severity="high",
        details=f"instruction_following: unknown requested_count_kind={kind!r}",
    )
