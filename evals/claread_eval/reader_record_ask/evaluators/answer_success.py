"""Dimension 1/11 — answer_success.

Spec: ``finalized.status == "ok"`` AND ``final_text`` non-empty AND no
``forbidden_answer_patterns`` substring. Failure ⇒ high severity.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

DIMENSION = "answer_success"


def evaluate_answer_success(
    case: ReaderRecordAskCase,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    reasons: list[str] = []

    if artifact.finalized_status != "ok":
        reasons.append(
            f"finalized_status={artifact.finalized_status!r} (expected 'ok')"
        )

    if not artifact.final_text:
        reasons.append("final_text is empty or None")

    if artifact.final_text:
        for pattern in case.expected.forbidden_answer_patterns:
            if pattern and pattern in artifact.final_text:
                reasons.append(
                    f"final_text contains forbidden pattern: {pattern!r}"
                )

    passed = not reasons
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity="none" if passed else "high",
        details=(
            "answer_success: status ok, text non-empty, no forbidden pattern"
            if passed
            else "; ".join(reasons)
        ),
        evidence_refs=[],
    )
