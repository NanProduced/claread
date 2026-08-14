"""Aggregator for reader-record-ask evaluator results.

Consumes a list of :class:`CaseEvalResult` (one per ``(case, run)`` pair)
plus the case lookup dict, and produces an :class:`AggregatedReport`
with:
- ``per_case``: the raw input list (for drill-down).
- ``per_dimension``: ``dim -> {passed, failed, total}``.
- ``per_config``: ``"model|thinking" -> {pass_rate, avg_latency,
  avg_tokens, total_requests, unsupported_claim_count,
  completeness_recall_avg, instruction_following_rate}``.
- ``failure_clusters``: ``(dimension, question_category,
  failure_pattern)`` groups with failed/total counts + case ids.

Key invariant (spec): **a deterministic failure is never overridden by
an LLM judge**. The aggregator uses ``EvalDimensionResult.passed`` as
the single source of truth — ``llm_judge_used`` / ``llm_judge_note``
are recording-only fields and are ignored when counting pass/fail and
when forming failure clusters.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    CLASSIFICATION_LEGACY as _REASON_LEGACY,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    CLASSIFICATION_SUPPORTED as _REASON_SUPPORTED,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS as _INSTRUMENTATION_INCOMPLETE_REASONS,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    MODEL_FAILURE_CLASSIFICATIONS as _MODEL_FAILURE_REASONS,
)
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase


class CaseEvalResult(BaseModel):
    """One (case, run) pair's full dimension results.

    Optional observability telemetry (``latency_seconds``,
    ``total_tokens``, ``total_requests``) is carried through from the
    :class:`RawArtifact` so ``per_config`` can compute averages without
    re-loading artifacts from disk.
    """

    model_config = {"extra": "forbid"}

    case_id: str
    run_id: str
    run_index: int
    model_short_name: str | None
    thinking_enabled: bool
    dimensions: list[EvalDimensionResult]
    latency_seconds: float | None = None
    total_tokens: int | None = None
    total_requests: int | None = None


class FailureCluster(BaseModel):
    """A grouped failure pattern for the report's "明确失败簇" section."""

    model_config = {"extra": "forbid"}

    dimension: str
    question_category: str
    failure_pattern: str  # e.g. "2025-year-hallucination"
    failed_count: int
    total_count: int
    case_ids: list[str] = Field(default_factory=list)


class AggregatedReport(BaseModel):
    """Top-level aggregated report across all (case, run) pairs."""

    model_config = {"extra": "forbid"}

    run_id: str
    total_cases: int
    total_runs: int
    per_case: list[CaseEvalResult]
    per_dimension: dict[str, dict[str, int]]  # dim -> {passed, failed, total}
    per_config: dict[str, dict[str, Any]]  # "model|thinking" -> metrics
    failure_clusters: list[FailureCluster] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RECALL_RE = re.compile(r"recall=([0-9.]+)")
_MISSING_RE = re.compile(r"missing=\[([^\]]*)\]")
_YEAR_IN_DETAILS_RE = re.compile(r"(?:19|20)\d{2}")


def _parse_recall(details: str) -> float | None:
    m = _RECALL_RE.search(details)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _extract_failure_pattern(dimension: str, details: str) -> str:
    """Derive a short, stable failure-pattern key from evaluator details.

    For ``context_support`` the
    typed ``classification`` field on :class:`EvalDimensionResult` is
    the SINGLE source of truth — see :func:`_extract_failure_pattern_typed`.
    This string-based fallback is kept only for dimensions that do
    not yet populate ``classification``.
    """
    if dimension == "unsupported_temporal_claims":
        m = _YEAR_IN_DETAILS_RE.search(details)
        if m:
            return f"{m.group(0)}-year-hallucination"
        return "temporal-claim-unsupported"
    if dimension == "exhaustive_completeness":
        m = _MISSING_RE.search(details)
        if m:
            missing = m.group(1).strip()
            # Use the first missing entity as the pattern signature.
            first = missing.split(",")[0].strip().strip("'\"")
            return f"missing-{first}" if first else "incomplete-enumeration"
        return "incomplete-enumeration"
    if dimension == "instruction_following":
        # Distinguish ``indeterminate`` (count could
        # not be determined) from ``actual_count_mismatch`` (count was
        # determined but did not match ``requested_count``). The
        # previous implementation grouped both under ``count-mismatch``,
        # causing the report to label indeterminate cases as
        # "生成 5 题" (a count-mismatch description).
        if "indeterminate" in details:
            return "indeterminate"
        if "actual_count_mismatch" in details:
            return "actual-count-mismatch"
        return "count-mismatch"
    if dimension == "entity_precision":
        return "type-confusion"
    if dimension == "numeric_grounding":
        return "unsupported-numeric"
    if dimension == "language_consistency":
        return "whole-sentence-english"
    if dimension == "evidence_minimality":
        return "evidence-minimality"
    if dimension == "tool_decision":
        return "tool-decision"
    if dimension == "usage_observability":
        return "observability-missing"
    if dimension == "context_support":
        # Typed classification is the SINGLE source of truth.
        # This fallback is only reached when ``classification`` is
        # None (e.g. legacy / metadata-only path). Defaulting to
        # ``fact-not-grounded`` here would mis-cluster
        # instrumentation blockers — the typed path
        # (:func:`_extract_failure_pattern_typed`) handles the
        # blocker distinction. ``fact-not-grounded`` is the safe
        # fallback for legacy artifacts (which the readiness audit
        # blocks at the verdict seam anyway).
        return "fact-not-grounded"
    if dimension == "answer_success":
        return "answer-failed"
    return "failure"


def _extract_failure_pattern_typed(
    dimension: str,
    details: str,
    classification: str | None,
) -> str:
    """Typed failure-pattern key.

    For ``context_support``, the typed ``classification`` field
    distinguishes:

    - **instrumentation blockers** (``baseline_unavailable`` /
      ``runtime_exception`` / ``instrumentation_incomplete``) →
      cluster pattern ``instrumentation-incomplete``. These do NOT
      enter rework and do NOT count as ``confirmed_model_failure``.
    - **real model correctness failures** (``fact_not_supported`` /
      ``fact_not_cited``) → cluster pattern ``fact-not-grounded``.
      These DO enter rework and DO count as ``confirmed_model_failure``.
    - **legacy artifact** (``legacy_artifact``) → cluster pattern
      ``legacy-artifact``. Replay classifies as
      ``indeterminate_requires_new_artifact``; authoritative aggregate
      blocks via the readiness audit.
    - **supported / None** → not a failure (no cluster).

    For all other dimensions, falls back to
    :func:`_extract_failure_pattern` (string-based details parsing).
    """
    if dimension == "context_support" and classification is not None:
        if classification in _INSTRUMENTATION_INCOMPLETE_REASONS:
            return "instrumentation-incomplete"
        if classification in _MODEL_FAILURE_REASONS:
            return "fact-not-grounded"
        if classification == _REASON_LEGACY:
            return "legacy-artifact"
        if classification == _REASON_SUPPORTED:
            # Should not reach here because ``passed=True`` skips
            # cluster formation. Defense-in-depth.
            return "supported"
    return _extract_failure_pattern(dimension, details)


def _identify_failure_clusters(
    case_results: list[CaseEvalResult],
    cases_by_id: dict[str, ReaderRecordAskCase],
) -> list[FailureCluster]:
    # (dimension, question_category, pattern) -> {failed, case_ids}
    cluster_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    # (dimension, question_category) -> total occurrences (pass + fail)
    total_map: dict[tuple[str, str], int] = {}

    for cr in case_results:
        case = cases_by_id.get(cr.case_id)
        qcat = case.question_category if case else "unknown"
        for dim in cr.dimensions:
            total_key = (dim.dimension, qcat)
            total_map[total_key] = total_map.get(total_key, 0) + 1

            if dim.passed:
                continue
            # Deterministic failure — LLM judge note is ignored.
            # Use typed
            # ``classification`` field when available so
            # instrumentation blockers do NOT cluster as
            # ``fact-not-grounded``.
            pattern = _extract_failure_pattern_typed(
                dim.dimension, dim.details, dim.classification
            )
            key = (dim.dimension, qcat, pattern)
            if key not in cluster_map:
                cluster_map[key] = {"failed_count": 0, "case_ids": []}
            cluster_map[key]["failed_count"] += 1
            if cr.case_id not in cluster_map[key]["case_ids"]:
                cluster_map[key]["case_ids"].append(cr.case_id)

    clusters: list[FailureCluster] = []
    for (dim_name, qcat, pattern), info in cluster_map.items():
        total = total_map.get((dim_name, qcat), info["failed_count"])
        clusters.append(
            FailureCluster(
                dimension=dim_name,
                question_category=qcat,
                failure_pattern=pattern,
                failed_count=info["failed_count"],
                total_count=total,
                case_ids=info["case_ids"],
            )
        )
    # Stable order: most failed first, then by key for determinism.
    clusters.sort(
        key=lambda c: (
            -c.failed_count,
            c.dimension,
            c.question_category,
            c.failure_pattern,
        )
    )
    return clusters


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def aggregate_results(
    case_results: list[CaseEvalResult],
    cases_by_id: dict[str, ReaderRecordAskCase],
) -> AggregatedReport:
    """Aggregate per-(case, run) dimension results into a report.

    The aggregator treats ``EvalDimensionResult.passed`` as
    authoritative. ``llm_judge_used`` / ``llm_judge_note`` are
    intentionally ignored — a deterministic failure is never overridden
    by a positive LLM judge note.
    """
    # per_dimension: dim -> {passed, failed, total}
    per_dimension: dict[str, dict[str, int]] = {}
    for cr in case_results:
        for dim in cr.dimensions:
            d = dim.dimension
            bucket = per_dimension.setdefault(
                d, {"passed": 0, "failed": 0, "total": 0}
            )
            bucket["total"] += 1
            # KEY: deterministic verdict — LLM judge cannot flip this.
            if dim.passed:
                bucket["passed"] += 1
            else:
                bucket["failed"] += 1

    # per_config: "model|thinking=..." -> metrics
    config_groups: dict[str, list[CaseEvalResult]] = {}
    for cr in case_results:
        key = f"{cr.model_short_name or 'unknown'}|thinking={cr.thinking_enabled}"
        config_groups.setdefault(key, []).append(cr)

    per_config: dict[str, dict[str, Any]] = {}
    for key, group in config_groups.items():
        total_runs = len(group)
        pass_count = 0
        latencies: list[float] = []
        tokens: list[int] = []
        total_requests = 0
        unsupported_count = 0
        recalls: list[float] = []
        instruction_pass = 0

        for cr in group:
            # A run "passes" iff every dimension passes.
            if all(dim.passed for dim in cr.dimensions):
                pass_count += 1
            if cr.latency_seconds is not None and cr.latency_seconds > 0:
                latencies.append(cr.latency_seconds)
            if cr.total_tokens is not None and cr.total_tokens > 0:
                tokens.append(cr.total_tokens)
            if cr.total_requests is not None and cr.total_requests > 0:
                total_requests += cr.total_requests

            for dim in cr.dimensions:
                if dim.dimension == "unsupported_temporal_claims" and not dim.passed:
                    unsupported_count += 1
                elif dim.dimension == "exhaustive_completeness":
                    # Passed runs always contribute recall=1.0
                    # (including ``requires_exhaustive_entity_recall=False``
                    # no-op passes). Failed runs keep the parsed recall
                    # from evaluator details; if unparseable, use 0.0 so
                    # failures still move the average.
                    if dim.passed:
                        recalls.append(1.0)
                    else:
                        parsed = _parse_recall(dim.details)
                        recalls.append(0.0 if parsed is None else parsed)
                elif (
                    dim.dimension == "instruction_following"
                    and dim.passed
                ):
                    instruction_pass += 1

        per_config[key] = {
            # ``total_runs`` MUST be explicitly written.
            # The previous implementation computed it
            # (``total_runs = len(group)``) but did NOT include it in the
            # per_config dict, causing the report generator to read 0
            # from the default and display "total_runs=0" despite 30
            # real runs.
            "total_runs": total_runs,
            "pass_rate": pass_count / total_runs if total_runs else 0.0,
            "avg_latency": sum(latencies) / len(latencies) if latencies else 0.0,
            "avg_tokens": sum(tokens) / len(tokens) if tokens else 0.0,
            "total_requests": total_requests,
            "unsupported_claim_count": unsupported_count,
            "completeness_recall_avg": sum(recalls) / len(recalls) if recalls else 0.0,
            "instruction_following_rate": (
                instruction_pass / total_runs if total_runs else 0.0
            ),
        }

    failure_clusters = _identify_failure_clusters(case_results, cases_by_id)

    run_id = case_results[0].run_id if case_results else ""

    return AggregatedReport(
        run_id=run_id,
        total_cases=len({cr.case_id for cr in case_results}),
        total_runs=len(case_results),
        per_case=case_results,
        per_dimension=per_dimension,
        per_config=per_config,
        failure_clusters=failure_clusters,
    )
