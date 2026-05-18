from __future__ import annotations

from typing import Any

ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION = "analysis_weighted_tokens_v1"
DICT_AI_FIXED_POINTS_POLICY_VERSION = "dict_ai_fixed_points_v1"
READER_ASK_WEIGHTED_TOKENS_POLICY_VERSION = ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION
DICT_AI_FIXED_POINTS = 5
READER_ASK_RESERVED_POINTS = 10

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


def compute_reader_ask_cost_points(usage_summary: dict[str, Any] | None) -> int:
    return compute_analysis_cost_points(usage_summary)


def build_reader_ask_billing_metadata(usage_summary: dict[str, Any] | None) -> dict[str, Any]:
    metadata = build_analysis_billing_metadata(usage_summary)
    metadata["billing_policy_version"] = READER_ASK_WEIGHTED_TOKENS_POLICY_VERSION
    metadata["reserved_points"] = READER_ASK_RESERVED_POINTS
    return metadata


def compute_dict_ai_cost_points(usage_summary: dict[str, Any] | None) -> int:
    _ = usage_summary
    return DICT_AI_FIXED_POINTS


def build_dict_ai_billing_metadata(usage_summary: dict[str, Any] | None) -> dict[str, Any]:
    aggregate = _extract_usage_aggregate(usage_summary)
    return {
        "input_tokens": int(aggregate.get("input_tokens") or 0),
        "output_tokens": int(aggregate.get("output_tokens") or 0),
        "total_tokens": int(aggregate.get("total_tokens") or 0),
        "fixed_points": DICT_AI_FIXED_POINTS,
        "billing_policy_version": DICT_AI_FIXED_POINTS_POLICY_VERSION,
    }
