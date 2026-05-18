from app.services.ai_usage import (
    READER_ASK_RESERVED_POINTS,
    READER_ASK_WEIGHTED_TOKENS_POLICY_VERSION,
    build_reader_ask_billing_metadata,
    compute_reader_ask_cost_points,
)


def test_compute_reader_ask_cost_points_uses_weighted_tokens() -> None:
    usage_summary = {
        "aggregate": {
            "input_tokens": 1200,
            "output_tokens": 200,
            "total_tokens": 1400,
        }
    }

    assert compute_reader_ask_cost_points(usage_summary) == 3


def test_build_reader_ask_billing_metadata_exposes_policy_and_reservation() -> None:
    usage_summary = {
        "aggregate": {
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
        }
    }

    metadata = build_reader_ask_billing_metadata(usage_summary)

    assert metadata["input_tokens"] == 10
    assert metadata["output_tokens"] == 20
    assert metadata["total_tokens"] == 30
    assert metadata["reserved_points"] == READER_ASK_RESERVED_POINTS
    assert metadata["billing_policy_version"] == READER_ASK_WEIGHTED_TOKENS_POLICY_VERSION
