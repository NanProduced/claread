"""Dimension 9/11 — evidence_minimality.

Spec (mirrors the grounding validator, scored independently):
- ``len(cited_evidence_handles) <= 6``
- handles non-duplicate (``set`` size == ``list`` size)
- every handle resolves to an entry in ``all_evidence_observations``
- type distribution: when ``baseline_is_complete=True`` and ALL cited
  handles are ``search_hit``, that is a soft (medium) issue — the
  baseline was already sufficient, the model should not have relied
  exclusively on search.

Hard failures (count / duplicate / unknown handle) ⇒ high severity.
Soft failure (type distribution only) ⇒ medium severity.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

DIMENSION = "evidence_minimality"

MAX_CITED_HANDLES = 6


def evaluate_evidence_minimality(
    case: ReaderRecordAskCase,  # noqa: ARG001 — signature contract
    artifact: RawArtifact,
) -> EvalDimensionResult:
    handles = artifact.cited_evidence_handles
    hard_failures: list[str] = []
    soft_failures: list[str] = []

    if len(handles) > MAX_CITED_HANDLES:
        hard_failures.append(
            f"too many handles: {len(handles)} > {MAX_CITED_HANDLES}"
        )

    if len(set(handles)) != len(handles):
        dup_count = len(handles) - len(set(handles))
        hard_failures.append(f"duplicate handles: {dup_count}")

    all_handle_ids = {ev.handle_id for ev in artifact.all_evidence_observations}
    unknown = [h for h in handles if h not in all_handle_ids]
    if unknown:
        hard_failures.append(f"handles not in observations: {unknown}")

    # Soft: type distribution when baseline is already complete.
    if artifact.baseline_is_complete is True and handles:
        obs_by_handle = {ev.handle_id: ev for ev in artifact.all_evidence_observations}
        kinds = [obs_by_handle[h].kind for h in handles if h in obs_by_handle]
        if kinds and all(k == "search_hit" for k in kinds):
            soft_failures.append(
                "all cited handles are search_hit while baseline_is_complete=True"
            )

    all_failures = hard_failures + soft_failures
    passed = not all_failures

    if not all_failures:
        severity = "none"
    elif hard_failures:
        severity = "high"
    else:
        severity = "medium"

    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity=severity,
        details=(
            f"evidence_minimality: {len(handles)} handles, all valid"
            if passed
            else "; ".join(all_failures)
        ),
        evidence_refs=list(handles),
    )
