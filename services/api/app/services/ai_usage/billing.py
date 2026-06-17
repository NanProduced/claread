from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION = "analysis_weighted_tokens_v1"
DICT_AI_FIXED_POINTS_POLICY_VERSION = "dict_ai_fixed_points_v1"
READER_ASK_WEIGHTED_TOKENS_POLICY_VERSION = ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION
DICT_AI_FIXED_POINTS = 5
READER_ASK_RESERVED_POINTS = 10

MULTIPLIER_INPUT = 1
MULTIPLIER_OUTPUT = 5
TOKENS_PER_POINT = 1000


class WeightedTokensBillingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    multiplier_input: int = Field(default=MULTIPLIER_INPUT, ge=0)
    multiplier_output: int = Field(default=MULTIPLIER_OUTPUT, ge=0)
    tokens_per_point: int = Field(default=TOKENS_PER_POINT, ge=1)
    price_multiplier: float = Field(default=1.0, gt=0.0)
    reserved_points: int = Field(default=0, ge=0)
    billing_policy_version: str = ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION


DEFAULT_ANALYSIS_BILLING_CONFIG = WeightedTokensBillingConfig(
    billing_policy_version=ANALYSIS_WEIGHTED_TOKENS_POLICY_VERSION,
)
DEFAULT_READER_ASK_BILLING_CONFIG = WeightedTokensBillingConfig(
    reserved_points=READER_ASK_RESERVED_POINTS,
    billing_policy_version=READER_ASK_WEIGHTED_TOKENS_POLICY_VERSION,
)


def _extract_usage_aggregate(usage_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not usage_summary:
        return {}
    aggregate = usage_summary.get("aggregate")
    if isinstance(aggregate, dict):
        return aggregate
    return usage_summary


def _compute_weighted_cost_points(
    usage_summary: dict[str, Any] | None,
    config: WeightedTokensBillingConfig,
) -> int:
    aggregate = _extract_usage_aggregate(usage_summary)
    if not aggregate:
        return 0

    input_tokens = int(aggregate.get("input_tokens") or 0)
    output_tokens = int(aggregate.get("output_tokens") or 0)
    weighted = (
        input_tokens * config.multiplier_input
        + output_tokens * config.multiplier_output
    )
    scaled_weighted = weighted * config.price_multiplier
    return int(math.ceil(scaled_weighted / config.tokens_per_point))


def _build_weighted_billing_metadata(
    usage_summary: dict[str, Any] | None,
    config: WeightedTokensBillingConfig,
) -> dict[str, Any]:
    aggregate = _extract_usage_aggregate(usage_summary)
    return {
        "input_tokens": int(aggregate.get("input_tokens") or 0),
        "output_tokens": int(aggregate.get("output_tokens") or 0),
        "total_tokens": int(aggregate.get("total_tokens") or 0),
        "multiplier_input": config.multiplier_input,
        "multiplier_output": config.multiplier_output,
        "tokens_per_point": config.tokens_per_point,
        "price_multiplier": config.price_multiplier,
        "reserved_points": config.reserved_points,
        "billing_policy_version": config.billing_policy_version,
    }


def compute_analysis_cost_points(
    usage_summary: dict[str, Any] | None,
    config: WeightedTokensBillingConfig | None = None,
) -> int:
    """
    Compute analysis points from aggregate token usage.

    Formula: ceil((input_tokens * 1 + output_tokens * 5) / 1000)
    """
    return _compute_weighted_cost_points(
        usage_summary,
        config or DEFAULT_ANALYSIS_BILLING_CONFIG,
    )


def build_analysis_billing_metadata(
    usage_summary: dict[str, Any] | None,
    config: WeightedTokensBillingConfig | None = None,
) -> dict[str, Any]:
    return _build_weighted_billing_metadata(
        usage_summary,
        config or DEFAULT_ANALYSIS_BILLING_CONFIG,
    )


def compute_reader_ask_cost_points(
    usage_summary: dict[str, Any] | None,
    config: WeightedTokensBillingConfig | None = None,
) -> int:
    return _compute_weighted_cost_points(
        usage_summary,
        config or DEFAULT_READER_ASK_BILLING_CONFIG,
    )


def build_reader_ask_billing_metadata(
    usage_summary: dict[str, Any] | None,
    config: WeightedTokensBillingConfig | None = None,
) -> dict[str, Any]:
    return _build_weighted_billing_metadata(
        usage_summary,
        config or DEFAULT_READER_ASK_BILLING_CONFIG,
    )


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
