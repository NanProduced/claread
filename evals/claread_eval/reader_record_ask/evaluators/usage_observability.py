"""Dimension 11/11 — usage_observability.

Spec: independently of content correctness, verify the run recorded the
telemetry needed for A/B aggregation and cost debugging:
- ``agent_usage`` not None and ``requests > 0``
- ``model_route`` non-empty
- ``latency_seconds`` not None and ``> 0``
- ``finalized_status`` not None
- ``thinking_enabled`` field present (modelled as ``bool``, so the only
  failure mode is the artifact itself being absent — covered by the
  other checks)

Any missing ⇒ ``passed=False`` severity=medium (independent
observability failure, not mixed with content-correctness scoring).
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

DIMENSION = "usage_observability"


def evaluate_usage_observability(
    case: ReaderRecordAskCase,  # noqa: ARG001 — signature contract
    artifact: RawArtifact,
) -> EvalDimensionResult:
    failures: list[str] = []

    if artifact.agent_usage is None:
        failures.append("agent_usage is None")
    else:
        requests = artifact.agent_usage.requests
        if requests is None or requests <= 0:
            failures.append(
                f"agent_usage.requests missing or <= 0: {requests!r}"
            )

    if not artifact.model_route:
        failures.append("model_route missing or empty")

    # thinking_enabled is a typed bool on RawArtifact; the only way it
    # is "missing" is if the artifact itself is malformed, in which case
    # pydantic would have rejected construction upstream. We rely on
    # the surrounding checks to flag a malformed artifact.

    if artifact.latency_seconds is None:
        failures.append("latency_seconds is None")
    elif artifact.latency_seconds <= 0:
        failures.append(f"latency_seconds <= 0: {artifact.latency_seconds}")

    if artifact.finalized_status is None:
        failures.append("finalized_status is None")

    passed = not failures
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity="none" if passed else "medium",
        details=(
            "usage_observability: usage, route, latency, status all recorded"
            if passed
            else "; ".join(failures)
        ),
        evidence_refs=[],
    )
