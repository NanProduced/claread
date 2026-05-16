from __future__ import annotations

from typing import Any

ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION = "analysis_weighted_tokens_v1"

MULTIPLIER_INPUT = 1
MULTIPLIER_OUTPUT = 5
TOKENS_PER_POINT = 1000


def _extract_usage_aggregate(usage_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not usage_summary:
        return {}
    aggregate = usage_summary.get("aggregate")
    if isinstance(aggregate, dict):
        return aggregate
    return usage_summary


def compute_analysis_cost_points(usage_summary: dict[str, Any] | None) -> int:
    """
    Compute analysis points from aggregate token usage.

    Formula: ceil((input_tokens * 1 + output_tokens * 5) / 1000)
    """
    aggregate = _extract_usage_aggregate(usage_summary)
    if not aggregate:
        return 0

    input_tokens = int(aggregate.get("input_tokens") or 0)
    output_tokens = int(aggregate.get("output_tokens") or 0)
    weighted = input_tokens * MULTIPLIER_INPUT + output_tokens * MULTIPLIER_OUTPUT
    return (weighted + TOKENS_PER_POINT - 1) // TOKENS_PER_POINT


def build_analysis_billing_metadata(usage_summary: dict[str, Any] | None) -> dict[str, Any]:
    aggregate = _extract_usage_aggregate(usage_summary)
    return {
        "input_tokens": int(aggregate.get("input_tokens") or 0),
        "output_tokens": int(aggregate.get("output_tokens") or 0),
        "total_tokens": int(aggregate.get("total_tokens") or 0),
        "multiplier_input": MULTIPLIER_INPUT,
        "multiplier_output": MULTIPLIER_OUTPUT,
        "tokens_per_point": TOKENS_PER_POINT,
        "billing_policy_version": ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION,
    }
