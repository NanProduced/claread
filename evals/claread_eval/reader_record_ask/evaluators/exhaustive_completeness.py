"""Dimension 6/11 — exhaustive_completeness.

Spec: for each type in ``expected_entity_set``, compute set recall =
|appeared in final_text| / |expected|. recall < 1.0 ⇒ high-severity
failure, details listing the missing entities (e.g. Thunder Bay).
Pairs with ``entity_precision`` (type purity) but is scored
independently.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskR4A3Case

DIMENSION = "exhaustive_completeness"


def evaluate_exhaustive_completeness(
    case: ReaderRecordAskR4A3Case,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    final_text = artifact.final_text or ""
    expected_set = case.expected.expected_entity_set

    failures: list[str] = []
    for type_name, entities in expected_set.items():
        if not entities:
            continue
        appeared = [e for e in entities if e in final_text]
        missing = [e for e in entities if e not in final_text]
        if missing:
            recall = len(appeared) / len(entities)
            failures.append(
                f"{type_name} recall={recall:.2f} missing={missing}"
            )

    passed = not failures
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity="none" if passed else "high",
        details=(
            "exhaustive_completeness: all expected entities present"
            if passed
            else "; ".join(failures)
        ),
        evidence_refs=[],
    )
