"""Dimension 8/11 — language_consistency.

Spec: when ``answer_language="zh"``, detect whole-sentence English that
is NOT a proper noun. Heuristic: split by sentence terminators; a
sentence whose ASCII-alpha ratio > 70% AND which is not just proper
nouns from the whitelist ⇒ medium-severity failure. Scattered English
words (``AI``, ``app``) are tolerated.
"""

from __future__ import annotations

import re

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

DIMENSION = "language_consistency"

# Proper-noun whitelist: ASCII tokens that may appear inside a zh answer
# without triggering a failure. Tuned for the Claread reader domain.
PROPER_NOUN_WHITELIST: tuple[str, ...] = (
    "BBC",
    "SpaceX",
    "OpenAI",
    "DeepSeek",
    "Thunder Bay",
    "AI",
    "app",
    "API",
)

SENTENCE_SPLIT_RE = re.compile(r"[。！？.!?]+")

# Ratio threshold for "this sentence is mostly English".
ENGLISH_RATIO_THRESHOLD = 0.7
# After removing whitelisted proper nouns, if the remaining text is
# STILL mostly English, it is a real whole-sentence-English failure.
REMAINING_ENGLISH_RATIO_THRESHOLD = 0.5


def _english_ratio(text: str) -> float:
    if not text:
        return 0.0
    alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    total = sum(1 for c in text if not c.isspace())
    return alpha / total if total else 0.0


def evaluate_language_consistency(
    case: ReaderRecordAskCase,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    final_text = artifact.final_text or ""

    if case.expected.answer_language != "zh":
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=True,
            severity="none",
            details=(
                f"language_consistency: answer_language="
                f"{case.expected.answer_language!r}, skip zh check"
            ),
        )

    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(final_text) if s.strip()]

    failures: list[str] = []
    for idx, sentence in enumerate(sentences, start=1):
        ratio = _english_ratio(sentence)
        if ratio <= ENGLISH_RATIO_THRESHOLD:
            continue
        # Strip whitelisted proper nouns and re-check: if it is still
        # mostly English, this is a genuine whole-sentence-English
        # violation rather than a Chinese sentence containing a proper
        # noun.
        cleaned = sentence
        for noun in PROPER_NOUN_WHITELIST:
            cleaned = cleaned.replace(noun, "")
        if _english_ratio(cleaned) > REMAINING_ENGLISH_RATIO_THRESHOLD:
            failures.append(
                f"sentence {idx} english_ratio={ratio:.2f}: "
                f"{sentence[:80]!r}"
            )

    passed = not failures
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity="none" if passed else "medium",
        details=(
            "language_consistency: zh answer has no whole-sentence english"
            if passed
            else "; ".join(failures)
        ),
        evidence_refs=[],
    )
