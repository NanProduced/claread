from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.numeric_grounding import (
    evaluate_numeric_grounding,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)


def _make_case(
    *,
    allowed_numerics: list[str] | None = None,
    question: str = "文章提到了哪些数据？",
) -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id="t-numeric",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question=question,
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(
            allowed_numerics=allowed_numerics or [],
        ),
    )


def _make_artifact(final_text: str) -> RawArtifact:
    return RawArtifact(
        case_id="t-numeric",
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
    )


def test_positive_quantified_number_in_allowed() -> None:
    case = _make_case(allowed_numerics=["858"])
    artifact = _make_artifact("文章提到 858 处火灾。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_positive_plain_integer_in_allowed() -> None:
    case = _make_case(allowed_numerics=["30"])
    artifact = _make_artifact("共 30 人参与。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_negative_unsupported_integer() -> None:
    case = _make_case(allowed_numerics=["858"])
    artifact = _make_artifact("文章提到 1000 处火灾。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "1000" in result.details


def test_negative_unsupported_percentage() -> None:
    case = _make_case(allowed_numerics=["30"])
    artifact = _make_artifact("增长了 50%。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is False
    assert "50" in result.details


def test_year_not_double_counted_as_numeric() -> None:
    # 2026 is a year token — handled by unsupported_temporal_claims,
    # not numeric_grounding. With allowed_numerics=["858"] the year
    # 2026 must NOT trigger a numeric failure.
    case = _make_case(allowed_numerics=["858"])
    artifact = _make_artifact("2026 年共 858 处火灾。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_ordinal_not_counted() -> None:
    # "第1题" — the 1 is an ordinal index, not a numeric claim.
    case = _make_case(allowed_numerics=[])
    artifact = _make_artifact("第1题：请简述文章主旨。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_question_number_implicitly_allowed() -> None:
    # "858" appears in the question; the answer quoting it is allowed.
    case = _make_case(
        allowed_numerics=[],
        question="文章提到的 858 处火灾发生在哪里？",
    )
    artifact = _make_artifact("858 处火灾发生在北部。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True
