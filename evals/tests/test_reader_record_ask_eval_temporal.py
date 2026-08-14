from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.unsupported_temporal_claims import (
    evaluate_unsupported_temporal_claims,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskExpected,
)


def _make_case(
    *,
    allowed: list[str] | None = None,
    must_declare_no_year: bool = False,
) -> ReaderRecordAskCase:
    return ReaderRecordAskCase(
        id="t-temporal",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown" if not allowed else "known_synthetic",
        baseline_mode="complete",
        question="文章是什么时候发布的？",
        question_category="publish_date",
        expected=ReaderRecordAskExpected(
            allowed_temporal_claims=allowed or [],
            must_declare_no_year=must_declare_no_year,
        ),
    )


def _make_artifact(final_text: str) -> RawArtifact:
    return RawArtifact(
        case_id="t-temporal",
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
    )


def test_positive_allowed_year_present() -> None:
    # source-known case: allowed=["2026"], final_text only contains 2026
    case = _make_case(allowed=["2026"])
    artifact = _make_artifact("文章发布于 2026 年。")
    result = evaluate_unsupported_temporal_claims(case, artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_positive_no_year_token_when_allowed_empty() -> None:
    # source-unknown case: allowed=[], final_text has no year token
    case = _make_case(allowed=[])
    artifact = _make_artifact("文章未提及具体发布年份。")
    result = evaluate_unsupported_temporal_claims(case, artifact)
    assert result.passed is True


def test_negative_year_not_in_allowed() -> None:
    # source-unknown: allowed=[], final_text contains "2025 年" → fail
    case = _make_case(allowed=[])
    artifact = _make_artifact("这篇文章发布于 2025 年。")
    result = evaluate_unsupported_temporal_claims(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "2025" in result.details
    assert "unsupported temporal tokens" in result.details


def test_source_known_vs_unknown_contrast() -> None:
    # Same final_text "2026 年发布", different allowed sets.
    text = "文章于 2026 年发布。"
    known_case = _make_case(allowed=["2026"])
    unknown_case = _make_case(allowed=[])
    known_result = evaluate_unsupported_temporal_claims(
        known_case, _make_artifact(text)
    )
    unknown_result = evaluate_unsupported_temporal_claims(
        unknown_case, _make_artifact(text)
    )
    assert known_result.passed is True
    assert unknown_result.passed is False
    assert "2026" in unknown_result.details


def test_must_declare_no_year_with_year_fails() -> None:
    case = _make_case(allowed=[], must_declare_no_year=True)
    artifact = _make_artifact("文章发布于 2025 年。")
    result = evaluate_unsupported_temporal_claims(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "must_declare_no_year" in result.details


def test_must_declare_no_year_without_declaration_fails() -> None:
    case = _make_case(allowed=[], must_declare_no_year=True)
    # No year token, but also no "未提供/未提及" declaration.
    artifact = _make_artifact("文章讨论了城市绿化。")
    result = evaluate_unsupported_temporal_claims(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "lacks no-year declaration" in result.details


def test_must_declare_no_year_with_declaration_passes() -> None:
    case = _make_case(allowed=[], must_declare_no_year=True)
    artifact = _make_artifact("文章未提供具体发布年份。")
    result = evaluate_unsupported_temporal_claims(case, artifact)
    assert result.passed is True


def test_relative_time_word_unsupported() -> None:
    case = _make_case(allowed=[])
    artifact = _make_artifact("这篇文章是去年发布的。")
    result = evaluate_unsupported_temporal_claims(case, artifact)
    assert result.passed is False
    assert "去年" in result.details
