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


# ---------------------------------------------------------------------------
# R4-A4-0 (Task 4) — structural numbering and CN date component masking.
# ---------------------------------------------------------------------------


def test_structural_list_marker_period_not_counted() -> None:
    # ``1.`` / ``2.`` / ``3.`` at start of line are structural numbering
    # (exercise items, enumerated answers). The digits inside are NOT
    # numeric claims about the article.
    case = _make_case(allowed_numerics=["858"])
    artifact = _make_artifact(
        "1. 文章提到 858 处火灾。\n2. 影响空气质量。\n3. 跨州扩散。"
    )
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_structural_list_marker_chinese_comma_not_counted() -> None:
    # ``1、`` / ``2、`` Chinese-style list markers.
    case = _make_case(allowed_numerics=["858"])
    artifact = _make_artifact(
        "1、文章提到 858 处火灾。\n2、影响空气质量。"
    )
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_structural_list_marker_paren_not_counted() -> None:
    # ``1)`` / ``2)`` parenthesis list markers.
    case = _make_case(allowed_numerics=["858"])
    artifact = _make_artifact(
        "1) 文章提到 858 处火灾。\n2) 影响空气质量。"
    )
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_reference_answer_list_markers_not_counted() -> None:
    # Reference answer section uses ``1.`` / ``2.`` markers — these are
    # structural, not numeric claims. The numbers inside the answer
    # (858, 30) must still be checked against allowed_numerics.
    case = _make_case(allowed_numerics=["858", "30"])
    artifact = _make_artifact(
        "参考答案：\n1. 858 处活跃野火，其中 30 起是新燃起的。\n2. 影响了多城市。"
    )
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_decimal_at_line_start_not_masked_as_list_marker() -> None:
    # ``1.5`` at start of line is a decimal, NOT a list marker. The
    # list-marker regex uses a lookahead ``(?=\s|$)`` so when ``.``
    # is followed by another digit (not whitespace), the marker pattern
    # must NOT fire — the digit ``1`` is then a numeric claim.
    case = _make_case(allowed_numerics=[])
    artifact = _make_artifact("1.5 倍的增长率。")
    result = evaluate_numeric_grounding(case, artifact)
    # ``1`` is not in allowed_numerics → must fail (not be masked).
    assert result.passed is False
    assert "1" in result.details


def test_cn_date_component_month_not_counted() -> None:
    # ``6月`` is a CN date component, not a numeric claim. The temporal
    # evaluator handles date tokens; numeric_grounding must not
    # double-penalize the digit.
    case = _make_case(allowed_numerics=["858"])
    artifact = _make_artifact("2025 年 6 月共 858 处火灾。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_cn_date_component_day_not_counted() -> None:
    # ``5日`` is a CN date component, not a numeric claim.
    case = _make_case(allowed_numerics=["858"])
    artifact = _make_artifact("2025 年 6 月 5 日共 858 处火灾。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_publish_date_hallucination_no_numeric_false_positive() -> None:
    # Regression: publish-date case 3/3 invents ``2025年6月5日或6日``.
    # The 2025 hallucination is caught by unsupported_temporal_claims.
    # numeric_grounding must NOT additionally flag the leftover ``6``,
    # ``5``, ``6`` from ``6月5日或6日`` as unsupported numerics.
    case = _make_case(
        allowed_numerics=[],
        question="文章是什么时候发生/发布的？",
    )
    artifact = _make_artifact(
        "文章报道的事件发生在2025年6月5日或6日前后。"
    )
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_exercise_answer_list_markers_not_counted() -> None:
    # Regression: BBC exercise-one answers use ``1.`` / ``2.`` markers.
    # Reference answer also uses markers. The factual numbers (858, 30)
    # must still be checked.
    case = _make_case(allowed_numerics=["858", "30"])
    artifact = _make_artifact(
        "1. 加拿大目前有多少起活跃的野火？\n2. 哪些城市发布警报？\n"
        "参考答案：\n1. 858 起活跃野火，其中 30 起是新燃起的。\n2. 多个城市。"
    )
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is True


def test_actual_hallucinated_number_still_fails() -> None:
    # Sanity: if the model invents a number that is NOT in
    # allowed_numerics and is NOT a structural marker / date / year /
    # ordinal, numeric_grounding must still fail.
    case = _make_case(allowed_numerics=["858"])
    artifact = _make_artifact("文章提到 999 处火灾。")
    result = evaluate_numeric_grounding(case, artifact)
    assert result.passed is False
    assert "999" in result.details
