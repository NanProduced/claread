from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from claread_eval.schemas.run import EvalCaseArtifact

AbVerdict = Literal["win", "loss", "tie", "manual_review"]


class AbCaseComparison(BaseModel):
    case_id: str
    verdict: AbVerdict
    baseline_hard_failures: int = 0
    candidate_hard_failures: int = 0
    baseline_soft_failures: int = 0
    candidate_soft_failures: int = 0
    baseline_status: str | None = None
    candidate_status: str | None = None
    identity_delta: dict[str, Any] | None = None
    reasons: list[str] = Field(default_factory=list)


class AbReport(BaseModel):
    baseline_run_id: str
    candidate_run_id: str
    baseline_dataset_id: str | None = None
    candidate_dataset_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    total_cases: int = 0
    wins: int = 0
    losses: int = 0
    ties: int = 0
    manual_review: int = 0
    regression_case_ids: list[str] = Field(default_factory=list)
    identity_warnings: list[str] = Field(default_factory=list)
    comparisons: list[AbCaseComparison] = Field(default_factory=list)


def _failure_counts(artifact: EvalCaseArtifact) -> tuple[int, int]:
    hard = 0
    soft = 0
    for result in artifact.grader_results:
        if result.get("verdict") != "fail":
            continue
        severity = result.get("severity")
        if severity == "hard":
            hard += 1
        elif severity == "soft":
            soft += 1
    return hard, soft


def compare_case_artifacts(
    baseline: EvalCaseArtifact,
    candidate: EvalCaseArtifact,
) -> AbCaseComparison:
    if baseline.case_id != candidate.case_id:
        raise ValueError(
            f"Cannot compare different cases: {baseline.case_id} vs {candidate.case_id}"
        )

    base_hard, base_soft = _failure_counts(baseline)
    cand_hard, cand_soft = _failure_counts(candidate)
    identity_delta = _identity_delta(baseline, candidate)
    reasons: list[str] = []

    if candidate.adapter_status != baseline.adapter_status:
        reasons.append(
            f"adapter_status changed: {baseline.adapter_status} -> {candidate.adapter_status}"
        )

    if cand_hard > base_hard:
        verdict: AbVerdict = "loss"
        reasons.append(f"hard failures increased: {base_hard} -> {cand_hard}")
    elif cand_hard < base_hard:
        verdict = "win"
        reasons.append(f"hard failures decreased: {base_hard} -> {cand_hard}")
    elif cand_soft > base_soft:
        verdict = "loss"
        reasons.append(f"soft failures increased: {base_soft} -> {cand_soft}")
    elif cand_soft < base_soft:
        verdict = "win"
        reasons.append(f"soft failures decreased: {base_soft} -> {cand_soft}")
    elif candidate.error and not baseline.error:
        verdict = "loss"
        reasons.append("candidate introduced adapter error")
    elif baseline.error and not candidate.error:
        verdict = "win"
        reasons.append("candidate fixed adapter error")
    elif candidate.adapter_status == "timeout" and baseline.adapter_status != "timeout":
        verdict = "loss"
        reasons.append("candidate introduced timeout")
    else:
        verdict = "tie"

    if not reasons:
        reasons.append("no deterministic delta")

    return AbCaseComparison(
        case_id=baseline.case_id,
        verdict=verdict,
        baseline_hard_failures=base_hard,
        candidate_hard_failures=cand_hard,
        baseline_soft_failures=base_soft,
        candidate_soft_failures=cand_soft,
        baseline_status=baseline.adapter_status,
        candidate_status=candidate.adapter_status,
        identity_delta=identity_delta,
        reasons=reasons,
    )


def build_ab_report(
    *,
    baseline_run_id: str,
    candidate_run_id: str,
    baseline_artifacts: list[EvalCaseArtifact],
    candidate_artifacts: list[EvalCaseArtifact],
    baseline_dataset_id: str | None = None,
    candidate_dataset_id: str | None = None,
) -> AbReport:
    baseline_by_id = {artifact.case_id: artifact for artifact in baseline_artifacts}
    candidate_by_id = {artifact.case_id: artifact for artifact in candidate_artifacts}
    shared_case_ids = sorted(set(baseline_by_id) & set(candidate_by_id))
    identity_warnings = _identity_warnings(
        baseline_by_id=baseline_by_id,
        candidate_by_id=candidate_by_id,
        shared_case_ids=shared_case_ids,
    )
    comparisons = [
        compare_case_artifacts(baseline_by_id[case_id], candidate_by_id[case_id])
        for case_id in shared_case_ids
    ]

    return AbReport(
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        baseline_dataset_id=baseline_dataset_id,
        candidate_dataset_id=candidate_dataset_id,
        total_cases=len(comparisons),
        wins=sum(1 for item in comparisons if item.verdict == "win"),
        losses=sum(1 for item in comparisons if item.verdict == "loss"),
        ties=sum(1 for item in comparisons if item.verdict == "tie"),
        manual_review=sum(1 for item in comparisons if item.verdict == "manual_review"),
        regression_case_ids=[
            item.case_id for item in comparisons if item.verdict == "loss"
        ],
        identity_warnings=identity_warnings,
        comparisons=comparisons,
    )


def _identity_snapshot(artifact: EvalCaseArtifact) -> dict[str, object]:
    return {
        "workflow_identity": artifact.workflow_identity.model_dump(mode="json"),
        "schema_identity": artifact.schema_identity.model_dump(mode="json"),
        "prompt_identity": artifact.prompt_identity.model_dump(mode="json"),
        "model_identity": artifact.model_identity.model_dump(mode="json"),
    }


def _identity_delta(
    baseline: EvalCaseArtifact,
    candidate: EvalCaseArtifact,
) -> dict[str, Any] | None:
    baseline_snapshot = _identity_snapshot(baseline)
    candidate_snapshot = _identity_snapshot(candidate)
    delta = {
        key: {
            "baseline": baseline_snapshot[key],
            "candidate": candidate_snapshot[key],
        }
        for key in baseline_snapshot
        if baseline_snapshot[key] != candidate_snapshot[key]
    }
    return delta or None


def _identity_warnings(
    *,
    baseline_by_id: dict[str, EvalCaseArtifact],
    candidate_by_id: dict[str, EvalCaseArtifact],
    shared_case_ids: list[str],
) -> list[str]:
    warnings: list[str] = []
    baseline_only = sorted(set(baseline_by_id) - set(candidate_by_id))
    candidate_only = sorted(set(candidate_by_id) - set(baseline_by_id))
    if baseline_only:
        warnings.append(f"baseline-only cases ignored: {', '.join(baseline_only)}")
    if candidate_only:
        warnings.append(f"candidate-only cases ignored: {', '.join(candidate_only)}")
    if not shared_case_ids:
        warnings.append("no shared case ids to compare")
        return warnings

    _append_internal_identity_warnings(
        warnings,
        side="baseline",
        artifacts=[baseline_by_id[case_id] for case_id in shared_case_ids],
    )
    _append_internal_identity_warnings(
        warnings,
        side="candidate",
        artifacts=[candidate_by_id[case_id] for case_id in shared_case_ids],
    )

    for key in ("workflow_identity", "schema_identity"):
        if any(
            _identity_snapshot(baseline_by_id[case_id])[key]
            != _identity_snapshot(candidate_by_id[case_id])[key]
            for case_id in shared_case_ids
        ):
            warnings.append(f"{key} differs between baseline and candidate")
    if all(
        _identity_snapshot(baseline_by_id[case_id])["prompt_identity"]
        == _identity_snapshot(candidate_by_id[case_id])["prompt_identity"]
        for case_id in shared_case_ids
    ):
        warnings.append("prompt_identity is identical; comparison may be replay/model/RAG delta")
    if any(
        _identity_snapshot(baseline_by_id[case_id])["model_identity"]
        != _identity_snapshot(candidate_by_id[case_id])["model_identity"]
        for case_id in shared_case_ids
    ):
        warnings.append("model_identity differs between baseline and candidate")
    return warnings


def _append_internal_identity_warnings(
    warnings: list[str],
    *,
    side: Literal["baseline", "candidate"],
    artifacts: list[EvalCaseArtifact],
) -> None:
    if not artifacts:
        return
    first = _identity_snapshot(artifacts[0])
    for artifact in artifacts[1:]:
        if _identity_snapshot(artifact) != first:
            warnings.append(f"{side} identity varies across shared cases")
            return
