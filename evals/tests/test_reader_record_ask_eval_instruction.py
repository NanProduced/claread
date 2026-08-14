"""Tests for instruction_following evaluator effectiveness.

Requirement: instruction count effectiveness.

Exercise item count semantics
================================================

Covers the new contract:

- Top-level numbered markers (1./2./3., 第N题, Q1) determine count.
- Unnumbered compound exercise block (multiple ``?``) defaults to
  count=1 (allow_subquestions=False).
- ``allow_subquestions=True`` allows each ``?`` to count as a separate
  item.
- Reference-answer numbering (after ``参考答案：``) is NOT counted.
- Multiple-choice options (A./B./C./D.) are options of ONE question.
- ``indeterminate`` and ``actual_count_mismatch`` use distinct failure
  patterns (distinguishable in details string).

Also retains backwards-compat coverage for previously-passing tests.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.instruction_following import (
    _strip_reference_answer,
    evaluate_instruction_following,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskExpected,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_case(
    *,
    requested_count: int | None,
    kind: str,
    question_category: str = "exercise_one",
    allow_subquestions: bool = False,
) -> ReaderRecordAskCase:
    return ReaderRecordAskCase(
        id="t-instruction",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="基于文章出一道小练习。",
        question_category=question_category,  # type: ignore[arg-type]
        expected=ReaderRecordAskExpected(
            requested_count=requested_count,
            requested_count_kind=kind,  # type: ignore[arg-type]
            allow_subquestions=allow_subquestions,
        ),
    )


def _make_artifact(final_text: str) -> RawArtifact:
    return RawArtifact(
        case_id="t-instruction",
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
    )


# ---------------------------------------------------------------------------
# Existing tests — must still pass (backwards compat)
# ---------------------------------------------------------------------------


def test_positive_exercise_items_count_matches() -> None:
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact("1. 文章的主旨是什么？请简述。")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_negative_exercise_items_count_too_many() -> None:
    """Count mismatch now uses ``actual_count_mismatch``
    pattern (distinct from ``indeterminate``).
    """
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact(
        "1. 文章主旨是什么？\n"
        "2. 作者观点是什么？\n"
        "3. 文章结构怎样？\n"
        "4. 哪些论据？\n"
        "5. 结论是什么？"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "actual=5" in result.details
    # Distinct failure pattern.
    assert "actual_count_mismatch" in result.details
    assert "indeterminate" not in result.details


def test_positive_sentences_within_limit() -> None:
    case = _make_case(requested_count=1, kind="sentences")
    artifact = _make_artifact("文章讨论了城市绿化的重要性。")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True


def test_negative_sentences_exceed_limit() -> None:
    """Sentence count mismatch now uses
    ``actual_count_mismatch`` pattern."""
    case = _make_case(requested_count=1, kind="sentences")
    artifact = _make_artifact(
        "文章讨论了城市绿化。作者强调了树木的重要性。最后给出了建议。"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "actual_count_mismatch" in result.details


def test_kind_none_always_passes() -> None:
    case = _make_case(requested_count=None, kind="none")
    artifact = _make_artifact("任意内容的回答。")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True


def test_q_marker_count() -> None:
    case = _make_case(requested_count=2, kind="exercise_items")
    artifact = _make_artifact("Q1: 文章主旨？\nQ2: 作者观点？")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True


def test_ordinal_topic_marker_count() -> None:
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact("第1题：文章主旨是什么？")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Regression: single unnumbered question → count as 1
# ---------------------------------------------------------------------------


def test_single_unnumbered_question_counted_as_one() -> None:
    """Spec: "单个未编号问句" should count as 1 exercise."""
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact("文章的主旨是什么？")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "single unnumbered interrogative" in result.details


def test_single_unnumbered_question_with_chinese_question_mark() -> None:
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact("请简述文章的核心观点？")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True


def test_single_unnumbered_question_with_ascii_question_mark() -> None:
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact("What is the main idea of the article?")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Regression: one question with A/B/C/D options → count as 1
# ---------------------------------------------------------------------------


def test_one_question_with_abcd_options_counted_as_one() -> None:
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact(
        "下列哪个是文章的主旨？\n"
        "A. 城市绿化的重要性\n"
        "B. 暴风雪的影响\n"
        "C. 经济发展的趋势\n"
        "D. 教育改革的方向"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "multiple-choice options detected" in result.details


def test_one_question_with_lowercase_abcd_options() -> None:
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact(
        "下列哪个是文章的主旨？\n"
        "a. 城市绿化\n"
        "b. 暴风雪\n"
        "c. 经济发展\n"
        "d. 教育改革"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Regression: five numbered questions → count as 5
# ---------------------------------------------------------------------------


def test_five_numbered_questions_counted_as_five() -> None:
    case = _make_case(requested_count=5, kind="exercise_items")
    artifact = _make_artifact(
        "1. 文章主旨是什么？\n"
        "2. 作者观点是什么？\n"
        "3. 文章结构怎样？\n"
        "4. 哪些论据？\n"
        "5. 结论是什么？"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=5" in result.details


# ---------------------------------------------------------------------------
# Regression: "第1题" and "1." together → NOT double counted
# ---------------------------------------------------------------------------


def test_ordinal_and_list_marker_not_double_counted() -> None:
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact("第1题：1. 文章主旨是什么？")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True


def test_ordinal_and_list_marker_on_newline_not_double_counted() -> None:
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact("第1题：\n1. 文章主旨是什么？")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=1" in result.details


# ---------------------------------------------------------------------------
# Regression: decimals and abbreviations not falsely split
# ---------------------------------------------------------------------------


def test_decimal_not_counted_as_list_item() -> None:
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact("文章提到约 1.5 倍增长，主旨是什么？")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "single unnumbered interrogative" in result.details


def test_decimal_in_sentence_count_not_split() -> None:
    case = _make_case(requested_count=1, kind="sentences")
    artifact = _make_artifact("文章提到约 1.5 倍增长。")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=1" in result.details


def test_multiple_decimals_in_sentence_count_not_split() -> None:
    case = _make_case(requested_count=1, kind="sentences")
    artifact = _make_artifact("GDP 增长 3.5%，失业率下降 1.2%。")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=1" in result.details


def test_abbreviation_not_split_in_sentence_count() -> None:
    case = _make_case(requested_count=1, kind="sentences")
    artifact = _make_artifact("文章讨论了城市绿化，i.e 树木的种植。")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=1" in result.details


# ---------------------------------------------------------------------------
# Indeterminate cases (truly undeterminable)
# ---------------------------------------------------------------------------


def test_no_markers_no_interrogative_is_indeterminate_fail() -> None:
    """Spec: "如无法可靠确定，输出 indeterminate，不能静默 PASS".

    When the answer has no exercise markers AND no interrogative
    punctuation, the count is indeterminate. The dimension must FAIL
    (not silently PASS) and use the ``indeterminate`` pattern.
    """
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact("文章讨论了城市绿化。")  # no markers, no ？
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is False
    assert result.severity == "medium"
    assert "indeterminate" in result.details
    assert "cannot reliably determine" in result.details
    # Indeterminate must NOT contain actual_count_mismatch.
    assert "actual_count_mismatch" not in result.details


def test_indeterminate_with_requested_zero_fails() -> None:
    case = _make_case(requested_count=0, kind="exercise_items")
    artifact = _make_artifact("文章讨论了城市绿化。")  # no markers
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is False
    assert "indeterminate" in result.details


# ---------------------------------------------------------------------------
# NEW — unnumbered compound block defaults to 1
# ---------------------------------------------------------------------------


def test_unnumbered_compound_block_defaults_to_one() -> None:
    """Spec: "Synthetic 单个未编号复合题 → 按显式 allow_subquestions 合同判定".

    With ``allow_subquestions=False`` (default), an unnumbered block
    with multiple ``?`` is ONE top-level exercise. The previous
    implementation marked this as ``indeterminate`` — a false positive.
    """
    case = _make_case(
        requested_count=1,
        kind="exercise_items",
        allow_subquestions=False,
    )
    # Multiple ``?`` but no numbered markers, no multiple-choice.
    artifact = _make_artifact(
        "请阅读文章并回答：文章主旨是什么？作者观点是什么？结论是什么？"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=1" in result.details
    assert "compound exercise block" in result.details
    assert "allow_subquestions=False" in result.details


def test_unnumbered_compound_block_with_allow_subquestions_true() -> None:
    """When ``allow_subquestions=True``, each ``?`` counts as a separate item."""
    case = _make_case(
        requested_count=3,
        kind="exercise_items",
        allow_subquestions=True,
    )
    # Three ``?`` → count=3 when allow_subquestions=True.
    artifact = _make_artifact(
        "请阅读文章并回答：文章主旨是什么？作者观点是什么？结论是什么？"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=3" in result.details
    assert "allow_subquestions=True" in result.details


def test_unnumbered_compound_block_with_allow_subquestions_true_mismatch() -> None:
    """When ``allow_subquestions=True`` and the count doesn't match,
    the failure uses ``actual_count_mismatch`` (not indeterminate).
    """
    case = _make_case(
        requested_count=1,
        kind="exercise_items",
        allow_subquestions=True,
    )
    # Three ``?`` but requested=1 → count=3 ≠ 1 → actual_count_mismatch.
    artifact = _make_artifact(
        "请阅读文章并回答：文章主旨是什么？作者观点是什么？结论是什么？"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "actual_count_mismatch" in result.details
    assert "actual=3" in result.details


# ---------------------------------------------------------------------------
# NEW — reference-answer numbering not counted
# ---------------------------------------------------------------------------


def test_reference_answer_numbering_not_counted_as_new_items() -> None:
    """Spec: "参考答案中的编号不得被误计为新题".

    The answer has one exercise question, then a "参考答案：" section
    with its own ``1. 2. 3.`` numbering. The count must be 1 (the
    exercise question), NOT 4 (1 question + 3 reference-answer items).
    """
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact(
        "1. 文章的主旨是什么？\n"
        "参考答案：\n"
        "1. 文章介绍了滨海市的自行车共享试点计划。\n"
        "2. 该计划在3个片区投放1200辆自行车。\n"
        "3. 试点半年后日均借车次数达到4500次。"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=1" in result.details


def test_reference_answer_marker_strips_section() -> None:
    """Unit test for ``_strip_reference_answer``."""
    text = "题目部分\n参考答案：\n1. ans1\n2. ans2"
    stripped = _strip_reference_answer(text)
    assert "参考答案" not in stripped
    assert "题目部分" in stripped


def test_no_reference_answer_marker_returns_text_unchanged() -> None:
    text = "1. 题目？"
    assert _strip_reference_answer(text) == text


# ---------------------------------------------------------------------------
# NEW — BBC 5/6 numbered questions → fail
# ---------------------------------------------------------------------------


def test_bbc_six_numbered_questions_fails_when_requested_one() -> None:
    """Spec: "BBC 5/6 个顶层编号题 → fail".

    The model generated 6 numbered exercises when the user asked for
    1. This is a real model failure (ignored the count constraint).
    The failure must use ``actual_count_mismatch`` pattern, NOT
    ``indeterminate``.
    """
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact(
        "1. 根据文章，Thunder Bay 位于哪个省份？\n"
        "2. 文章提到的火灾影响了哪些地区的空气质量？\n"
        "3. 文章中 858 这个数字代表什么？\n"
        "4. 为什么纽约州的空气质量会受到影响？\n"
        "5. 文章中提到的 30 指的是什么？\n"
        "6. 文章主旨是什么？"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "actual_count_mismatch" in result.details
    assert "actual=6" in result.details
    assert "indeterminate" not in result.details


def test_bbc_five_numbered_questions_fails_when_requested_one() -> None:
    """Same as above but with 5 numbered items (also a real failure)."""
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact(
        "1. 文章主旨是什么？\n"
        "2. 作者观点是什么？\n"
        "3. 文章结构怎样？\n"
        "4. 哪些论据？\n"
        "5. 结论是什么？"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "actual_count_mismatch" in result.details
    assert "actual=5" in result.details


# ---------------------------------------------------------------------------
# NEW — multiple `?` does NOT auto-equal multiple items
# ---------------------------------------------------------------------------


def test_multiple_question_marks_not_auto_multiple_items() -> None:
    """Spec: "多问号不自动等于多题".

    An unnumbered block with 4 ``?`` defaults to count=1
    (allow_subquestions=False). The previous implementation marked
    this as ``indeterminate`` with 4 interrogative markers — a false
    positive.
    """
    case = _make_case(
        requested_count=1,
        kind="exercise_items",
        allow_subquestions=False,
    )
    artifact = _make_artifact(
        "请阅读以下文章并回答：什么时候？在哪里？谁？为什么？"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=1" in result.details


# ---------------------------------------------------------------------------
# NEW — single question + A/B/C/D = 1
# ---------------------------------------------------------------------------


def test_single_question_with_abcd_is_one_item() -> None:
    """Spec: "单题 + A/B/C/D → count=1".

    The previous implementation handled this correctly, but we add
    an explicit test to lock the contract.
    """
    case = _make_case(requested_count=1, kind="exercise_items")
    artifact = _make_artifact(
        "下列哪项是文章中提到的城市？\n"
        "A. Thunder Bay\n"
        "B. 纽约\n"
        "C. 多伦多\n"
        "D. 芝加哥"
    )
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=1" in result.details
    assert "multiple-choice options detected" in result.details


# ---------------------------------------------------------------------------
# Sentence counting edge cases
# ---------------------------------------------------------------------------


def test_empty_text_zero_sentences() -> None:
    case = _make_case(requested_count=0, kind="sentences")
    artifact = _make_artifact("")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=0" in result.details


def test_no_punctuation_treated_as_one_sentence() -> None:
    case = _make_case(requested_count=1, kind="sentences")
    artifact = _make_artifact("文章讨论了城市绿化")
    result = evaluate_instruction_following(case, artifact)
    assert result.passed is True
    assert "actual=1" in result.details


def test_chinese_and_ascii_mixed_sentence_boundaries() -> None:
    case = _make_case(requested_count=2, kind="sentences")
    artifact = _make_artifact("第一句。Second sentence. Third sentence。")
    result = evaluate_instruction_following(case, artifact)
    assert "actual=3" in result.details
    assert result.passed is False  # 3 > 2
    assert "actual_count_mismatch" in result.details


# Note: The "unknown requested_count_kind → FAIL" branch in the evaluator
# is defensive dead code — the Pydantic schema enforces
# ``requested_count_kind: Literal["exercise_items", "sentences", "none"]``,
# so an invalid kind cannot reach the evaluator through normal data
# loading. The schema validation itself is the guard, so no runtime test
# is needed here.
