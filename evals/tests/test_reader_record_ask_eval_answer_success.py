from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.answer_success import (
    evaluate_answer_success,
)
from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskExpected,
)


def _make_case(
    *,
    forbidden_patterns: list[str] | None = None,
) -> ReaderRecordAskCase:
    return ReaderRecordAskCase(
        id="t-answer-success",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="这篇文章主要说了什么？",
        question_category="main_idea",
        expected=ReaderRecordAskExpected(
            forbidden_answer_patterns=forbidden_patterns or [],
        ),
    )


def _make_artifact(
    *,
    finalized_status: str | None = "ok",
    final_text: str | None = "文章讲述了城市绿化的发展。",
) -> RawArtifact:
    return RawArtifact(
        case_id="t-answer-success",
        run_id="run-1",
        finalized_status=finalized_status,
        final_text=final_text,
    )


def test_positive_ok_no_forbidden_pattern() -> None:
    case = _make_case(forbidden_patterns=["无法读取当前文章"])
    artifact = _make_artifact()
    result = evaluate_answer_success(case, artifact)
    assert result.passed is True
    assert result.severity == "none"
    assert "ok" in result.details


def test_negative_finalized_status_unavailable() -> None:
    case = _make_case()
    artifact = _make_artifact(finalized_status="unavailable", final_text=None)
    result = evaluate_answer_success(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "unavailable" in result.details
    assert "empty" in result.details


def test_negative_forbidden_pattern_in_text() -> None:
    case = _make_case(forbidden_patterns=["无法读取当前文章"])
    artifact = _make_artifact(final_text="无法读取当前文章，请稍后再试。")
    result = evaluate_answer_success(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "无法读取当前文章" in result.details


def test_negative_empty_final_text() -> None:
    case = _make_case()
    artifact = _make_artifact(final_text="")
    result = evaluate_answer_success(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "empty" in result.details
