"""Dimension 7/11 — instruction_following.

Requirement: instruction count effectiveness.

Exercise item count semantics
================================================

The previous implementation had two bugs:

1. **Multi-``?`` indeterminate false positive**: an unnumbered
   single exercise block containing multiple related sub-questions
   (separated by ``?``) was marked ``indeterminate`` and FAILED. Per
   the new contract, an unnumbered block is ONE top-level exercise by
   default; only an explicit ``allow_subquestions: true`` dataset
   field allows each ``?`` to count as a separate item.

2. **Reference-answer numbering double-count**: when the model's
   answer contained a "参考答案" section with its own ``1. 2. 3.``
   numbering, those reference-answer numbers were counted as
   additional exercise items, inflating the count. The new contract
   strips the reference-answer section before counting.

Frozen product semantics (spec: "冻结产品语义"):

- Top-level numbered markers (``1. 2. 3.``, ``第1题``, ``Q1``) are
  the AUTHORITATIVE signal for exercise item count. When present,
  they determine the count regardless of ``allow_subquestions``.
- An unnumbered single exercise block, even with multiple related
  sub-questions, defaults to ONE top-level exercise item.
- ``allow_subquestions: true`` (explicit dataset field) allows each
  ``?`` in an unnumbered block to count as a separate item.
- Multiple-choice options (``A./B./C./D.``) are options of ONE
  question, not separate exercises.
- Reference-answer numbering (after a ``参考答案`` marker) is NOT
  counted as new exercise items.

Failure pattern separation (spec: "indeterminate 与
actual_count_mismatch 必须用不同 failure pattern"):

- ``indeterminate``: count truly cannot be determined (no markers,
  no interrogatives, no multiple-choice options). Severity=medium.
- ``actual_count_mismatch``: count was determined but does not match
  ``requested_count``. Severity=high.

The aggregator's ``_extract_failure_pattern`` distinguishes these
two via the details string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

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

# Reference-answer section marker. When the answer
# contains a "参考答案" / "答案" / "参考解答" header, everything AFTER
# the marker is the reference answer and must NOT be counted as new
# exercise items. The marker itself is the boundary.
REFERENCE_ANSWER_MARKER_RE = re.compile(
    r"(?:参考答案|参考解答|答案|参考答复)\s*[:：]",
)

# Sentence boundary: Chinese 。！？ always; ASCII .!? only when NOT
# between two alphanumeric characters (avoids splitting decimals like
# "1.5" and abbreviations like "e.g.").
SENTENCE_BOUNDARY_RE = re.compile(
    r"[。！？]+|(?<![a-zA-Z0-9])[.!?]+|[.!?]+(?![a-zA-Z0-9])"
)


@dataclass(frozen=True)
class _CountResult:
    """Structured result of counting exercise items or sentences.

    ``count is None`` means ``indeterminate`` — the evaluator cannot
    reliably determine the count from the text. In that case, if a
    count constraint exists, the dimension FAILs (never silently PASS).

    ``failure_kind`` distinguishes indeterminate from
    actual_count_mismatch at the evaluator-output level so the
    aggregator's failure-pattern extractor can produce distinct
    patterns. Values: ``"determinate"`` / ``"indeterminate"``.
    """

    count: int | None
    reason: str
    failure_kind: str = "determinate"


def _strip_reference_answer(text: str) -> str:
    """Strip the reference-answer section from ``text``.

    When the model's answer includes a "参考答案："
    or equivalent marker, everything after the marker is the reference
    answer (sample solution) and its ``1. 2. 3.`` numbering must NOT
    be counted as new exercise items.

    Returns the text up to (but not including) the first reference-
    answer marker. If no marker is present, returns ``text`` unchanged.
    """
    m = REFERENCE_ANSWER_MARKER_RE.search(text)
    if m is None:
        return text
    return text[: m.start()]


def _count_exercise_items(
    text: str,
    *,
    allow_subquestions: bool = False,
) -> _CountResult:
    """Count distinct exercise items in ``text``.

    Contract:

    - Top-level numbered markers (Q1, 第N题, 1.) are AUTHORITATIVE.
      When present, they determine the count via max-across-signals
      deduplication. ``allow_subquestions`` is ignored in this branch.
    - When NO numbered markers are present:
      - Multiple-choice options (A./B./C./D.) → count=1 (one question
        with options).
      - Single interrogative (one ？/?) → count=1.
      - Multiple interrogatives + ``allow_subquestions=False``
        (default) → count=1 (one compound exercise block).
      - Multiple interrogatives + ``allow_subquestions=True`` →
        count = number of interrogatives.
      - No markers, no interrogatives, no multiple-choice →
        ``indeterminate``.
    """
    # Strip the reference-answer section before
    # counting so its ``1. 2. 3.`` numbering is not误计为新题.
    counting_text = _strip_reference_answer(text)

    signals: list[int] = []

    q_matches = {m.group(1) for m in Q_MARK_RE.finditer(counting_text)}
    if q_matches:
        signals.append(len(q_matches))

    ordinal_matches = {m.group(1) for m in ORDINAL_TOPIC_RE.finditer(counting_text)}
    if ordinal_matches:
        signals.append(len(ordinal_matches))

    list_matches = LIST_ITEM_RE.findall(counting_text)
    if list_matches:
        # Distinct list indices (deduplicate "1." appearing multiple times)
        signals.append(len(set(list_matches)))

    if signals:
        return _CountResult(
            count=max(signals),
            reason=f"numbered markers detected (signals={signals})",
        )

    # No numbered markers — try multiple-choice options.
    if MULTIPLE_CHOICE_RE.search(counting_text):
        return _CountResult(
            count=1,
            reason="multiple-choice options detected (one question with options)",
        )

    # Try interrogative sentences.
    interrogatives = INTERROGATIVE_RE.findall(counting_text)
    if len(interrogatives) == 1:
        return _CountResult(
            count=1,
            reason="single unnumbered interrogative sentence",
        )
    if len(interrogatives) > 1:
        if allow_subquestions:
            return _CountResult(
                count=len(interrogatives),
                reason=(
                    f"allow_subquestions=True; {len(interrogatives)} "
                    "interrogative markers counted as separate items"
                ),
            )
        # Default behavior — an unnumbered block
        # with multiple related sub-questions is ONE top-level
        # exercise. The previous implementation returned indeterminate
        # here, which was a false positive.
        return _CountResult(
            count=1,
            reason=(
                f"unnumbered compound exercise block ({len(interrogatives)} "
                "sub-questions) counted as 1 top-level exercise "
                "(allow_subquestions=False)"
            ),
        )

    # No markers at all — cannot reliably determine.
    return _CountResult(
        count=None,  # indeterminate
        reason=(
            "no exercise markers and no interrogative punctuation; "
            "cannot reliably determine count"
        ),
        failure_kind="indeterminate",
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
    case: ReaderRecordAskCase,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    """Evaluate whether the answer satisfies the requested count constraint.

    See module docstring for the full contract. When the count is
    ``indeterminate`` and a constraint exists, the dimension FAILs
    with ``severity="medium"`` and details containing ``indeterminate``.
    When the count is determined but does not match ``requested_count``,
    the dimension FAILs with ``severity="high"`` and details containing
    ``actual_count_mismatch``.
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
        result = _count_exercise_items(
            final_text,
            allow_subquestions=case.expected.allow_subquestions,
        )
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
        if passed:
            return EvalDimensionResult(
                dimension=DIMENSION,
                passed=True,
                severity="none",
                details=(
                    f"instruction_following: exercise_items actual={result.count} "
                    f"requested={requested}; {result.reason}"
                ),
            )
        # Distinct failure pattern for count mismatch.
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=False,
            severity="high",
            details=(
                f"instruction_following: exercise_items actual_count_mismatch; "
                f"actual={result.count} requested={requested}; {result.reason}"
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
        if passed:
            return EvalDimensionResult(
                dimension=DIMENSION,
                passed=True,
                severity="none",
                details=(
                    f"instruction_following: sentences actual={result.count} "
                    f"requested<={requested}; {result.reason}"
                ),
            )
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=False,
            severity="high",
            details=(
                f"instruction_following: sentences actual_count_mismatch; "
                f"actual={result.count} requested<={requested}; {result.reason}"
            ),
        )

    # Unknown kind — fail safe.
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=False,
        severity="high",
        details=f"instruction_following: unknown requested_count_kind={kind!r}",
    )
