from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact, RawUsage
from claread_eval.reader_record_ask.evaluators.usage_observability import (
    evaluate_usage_observability,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskExpected,
)


def _make_case() -> ReaderRecordAskCase:
    return ReaderRecordAskCase(
        id="t-usage",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="这篇文章主要说了什么？",
        question_category="main_idea",
        expected=ReaderRecordAskExpected(),
    )


_UNSET = object()


def _default_usage() -> RawUsage:
    return RawUsage(requests=3, input_tokens=1200, output_tokens=400)


def _make_artifact(
    *,
    agent_usage: object = _UNSET,
    model_route: str | None = "deepseek",
    latency_seconds: float | None = 12.5,
    finalized_status: str | None = "ok",
) -> RawArtifact:
    if agent_usage is _UNSET:
        agent_usage = _default_usage()
    return RawArtifact(
        case_id="t-usage",
        run_id="run-1",
        model_route=model_route,
        thinking_enabled=False,
        agent_usage=agent_usage,  # type: ignore[arg-type]
        latency_seconds=latency_seconds,
        finalized_status=finalized_status,
        final_text="回答",
    )


def test_positive_all_observability_present() -> None:
    artifact = _make_artifact()
    result = evaluate_usage_observability(_make_case(), artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_negative_agent_usage_none() -> None:
    artifact = _make_artifact(agent_usage=None)
    result = evaluate_usage_observability(_make_case(), artifact)
    assert result.passed is False
    assert result.severity == "medium"
    assert "agent_usage is None" in result.details


def test_negative_requests_zero() -> None:
    artifact = _make_artifact(
        agent_usage=RawUsage(requests=0, input_tokens=10, output_tokens=5)
    )
    result = evaluate_usage_observability(_make_case(), artifact)
    assert result.passed is False
    assert result.severity == "medium"
    assert "requests" in result.details


def test_negative_model_route_empty() -> None:
    artifact = _make_artifact(model_route="")
    result = evaluate_usage_observability(_make_case(), artifact)
    assert result.passed is False
    assert "model_route" in result.details


def test_negative_latency_none() -> None:
    artifact = _make_artifact(latency_seconds=None)
    result = evaluate_usage_observability(_make_case(), artifact)
    assert result.passed is False
    assert "latency_seconds" in result.details


def test_negative_latency_zero() -> None:
    artifact = _make_artifact(latency_seconds=0.0)
    result = evaluate_usage_observability(_make_case(), artifact)
    assert result.passed is False
    assert "latency_seconds <= 0" in result.details


def test_negative_finalized_status_none() -> None:
    artifact = _make_artifact(finalized_status=None)
    result = evaluate_usage_observability(_make_case(), artifact)
    assert result.passed is False
    assert "finalized_status" in result.details


def test_observability_independent_of_content_correctness() -> None:
    # Even an "unavailable" finalized status is observable; the
    # dimension only checks telemetry presence, not answer correctness.
    artifact = _make_artifact(finalized_status="unavailable")
    result = evaluate_usage_observability(_make_case(), artifact)
    assert result.passed is True
