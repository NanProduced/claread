from app.services.ai_usage import (
    DICT_AI_FIXED_POINTS,
    DICT_AI_FIXED_POINTS_POLICY_VERSION,
    build_dict_ai_billing_metadata,
    compute_dict_ai_cost_points,
)


def test_compute_dict_ai_cost_points_returns_fixed_price() -> None:
    usage_summary = {
        "aggregate": {
            "input_tokens": 1200,
            "output_tokens": 200,
            "total_tokens": 1400,
        }
    }

    assert compute_dict_ai_cost_points(usage_summary) == DICT_AI_FIXED_POINTS


def test_build_dict_ai_billing_metadata_exposes_policy_version() -> None:
    usage_summary = {
        "aggregate": {
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
        }
    }

    metadata = build_dict_ai_billing_metadata(usage_summary)

    assert metadata["input_tokens"] == 10
    assert metadata["output_tokens"] == 20
    assert metadata["total_tokens"] == 30
    assert metadata["fixed_points"] == DICT_AI_FIXED_POINTS
    assert metadata["billing_policy_version"] == DICT_AI_FIXED_POINTS_POLICY_VERSION
