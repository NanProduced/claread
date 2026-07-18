from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.tool_decision import (
    evaluate_tool_decision,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)


def _make_case(expect_tool_calls: str) -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id="t-tool-decision",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="这篇文章主要说了什么？",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(expect_tool_calls=expect_tool_calls),  # type: ignore[arg-type]
    )


def _make_artifact(
    *,
    read_range_calls: int = 0,
    search_calls: int = 0,
    baseline_is_complete: bool | None = True,
) -> RawArtifact:
    return RawArtifact(
        case_id="t-tool-decision",
        run_id="run-1",
        finalized_status="ok",
        final_text="回答",
        read_range_calls=read_range_calls,
        search_current_article_calls=search_calls,
        baseline_is_complete=baseline_is_complete,
    )


def test_positive_forbidden_no_calls_when_baseline_complete() -> None:
    case = _make_case("forbidden")
    artifact = _make_artifact(
        read_range_calls=0, search_calls=0, baseline_is_complete=True
    )
    result = evaluate_tool_decision(case, artifact)
    assert result.passed is True
    assert result.severity == "none"
    assert "read_range_calls=0" in result.details
    assert "search_current_article_calls=0" in result.details


def test_negative_forbidden_but_calls_made() -> None:
    case = _make_case("forbidden")
    artifact = _make_artifact(
        read_range_calls=1, search_calls=0, baseline_is_complete=True
    )
    result = evaluate_tool_decision(case, artifact)
    assert result.passed is False
    assert result.severity == "medium"


def test_positive_optional_any_calls() -> None:
    case = _make_case("optional")
    artifact = _make_artifact(read_range_calls=2, search_calls=1)
    result = evaluate_tool_decision(case, artifact)
    assert result.passed is True
    assert "read_range_calls=2" in result.details


def test_positive_required_calls_made() -> None:
    case = _make_case("required")
    artifact = _make_artifact(
        read_range_calls=1, search_calls=0, baseline_is_complete=False
    )
    result = evaluate_tool_decision(case, artifact)
    assert result.passed is True


def test_negative_required_no_calls_baseline_incomplete() -> None:
    case = _make_case("required")
    artifact = _make_artifact(
        read_range_calls=0, search_calls=0, baseline_is_complete=False
    )
    result = evaluate_tool_decision(case, artifact)
    assert result.passed is False
    assert result.severity == "medium"
    assert "baseline_is_complete=False" in result.details
    assert "none made" in result.details


def test_details_always_carries_call_counts() -> None:
    case = _make_case("optional")
    artifact = _make_artifact(read_range_calls=3, search_calls=2)
    result = evaluate_tool_decision(case, artifact)
    assert "read_range_calls=3" in result.details
    assert "search_current_article_calls=2" in result.details
