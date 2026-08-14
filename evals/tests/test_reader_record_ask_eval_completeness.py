"""Tests for exhaustive_completeness evaluator.

Explicit recall scope + alias contract
======================================

The previous implementation unconditionally required every entity in
``expected_entity_set`` to appear in the answer. The current contract replaces this
with an explicit opt-in contract:

- Recall is enforced ONLY when
  expected-data ``requires_exhaustive_entity_recall`` field
  is ``True``.
- Entity entries support ``|``-separated alias lists so ``雷霆湾``
  matches ``Thunder Bay``.

Covers:
- City enumeration (exhaustive=True) → full recall check.
- Main idea with entity set but exhaustive=False → does NOT fail on
  missing cities.
- ``雷霆湾`` alias hits ``Thunder Bay``.
- A genuinely missing city still fails (when exhaustive=True).
- A non-city type cannot masquerade as a city type.
- Empty entity set still passes (vacuous).
- Multiple types with mixed recall.
- Default behavior (no flag set) → exhaustive=False.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.exhaustive_completeness import (
    _entity_aliases,
    _entity_in_text,
    evaluate_exhaustive_completeness,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskExpected,
)


def _make_case(
    entity_set: dict[str, list[str]],
    *,
    requires_exhaustive: bool = False,
    question_category: str = "city_enumeration",
) -> ReaderRecordAskCase:
    return ReaderRecordAskCase(
        id="t-completeness",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category=question_category,  # type: ignore[arg-type]
        expected=ReaderRecordAskExpected(
            expected_entity_set=entity_set,
            requires_exhaustive_entity_recall=requires_exhaustive,
        ),
    )


def _make_artifact(final_text: str) -> RawArtifact:
    return RawArtifact(
        case_id="t-completeness",
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
    )


# ---------------------------------------------------------------------------
# Alias helpers
# ---------------------------------------------------------------------------


def test_entity_aliases_splits_pipe_separator() -> None:
    assert _entity_aliases("Thunder Bay|雷霆湾|桑德贝") == [
        "Thunder Bay",
        "雷霆湾",
        "桑德贝",
    ]
    assert _entity_aliases("Thunder Bay") == ["Thunder Bay"]
    assert _entity_aliases("") == []
    assert _entity_aliases("Thunder Bay|") == ["Thunder Bay"]
    assert _entity_aliases("  Thunder Bay  |  雷霆湾  ") == [
        "Thunder Bay",
        "雷霆湾",
    ]


def test_entity_in_text_matches_any_alias() -> None:
    assert _entity_in_text("Thunder Bay|雷霆湾|桑德贝", "文章提到了雷霆湾。")
    assert _entity_in_text("Thunder Bay|雷霆湾|桑德贝", "Thunder Bay is a city.")
    assert _entity_in_text("Thunder Bay|雷霆湾|桑德贝", "桑德贝位于加拿大。")
    assert not _entity_in_text("Thunder Bay|雷霆湾|桑德贝", "文章提到了多伦多。")
    # Case-insensitive for Latin
    assert _entity_in_text("Thunder Bay", "the city of THUNDER BAY")


# ---------------------------------------------------------------------------
# requires_exhaustive_entity_recall contract
# ---------------------------------------------------------------------------


def test_positive_all_entities_present_when_exhaustive_required() -> None:
    """City enumeration with exhaustive=True → full recall check passes."""
    case = _make_case(
        {"city": ["Thunder Bay", "Toronto", "Vancouver", "Montreal", "Ottawa"]},
        requires_exhaustive=True,
    )
    artifact = _make_artifact(
        "文章提到的城市包括 Thunder Bay、Toronto、Vancouver、Montreal 和 Ottawa。"
    )
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_negative_missing_thunder_bay_recall_below_one_when_exhaustive() -> None:
    """5 expected, 1 (Thunder Bay) missing → recall=0.8 → FAIL."""
    case = _make_case(
        {"city": ["Thunder Bay", "Toronto", "Vancouver", "Montreal", "Ottawa"]},
        requires_exhaustive=True,
    )
    artifact = _make_artifact(
        "文章提到的城市包括 Toronto、Vancouver、Montreal 和 Ottawa。"
    )
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "Thunder Bay" in result.details
    assert "recall=0.80" in result.details


def test_main_idea_with_entity_set_does_not_fail_when_not_exhaustive() -> None:
    """Spec: "main idea 带 entity catalog → 不因缺少城市而失败".

    A main_idea case may declare an entity set for context, but
    because the user asked "what is the main idea?" (NOT "list all
    cities"), recall is NOT enforced. The dimension must pass even
    if the answer mentions no cities at all.
    """
    case = _make_case(
        {"city": ["Thunder Bay", "Toronto", "Vancouver"]},
        requires_exhaustive=False,
        question_category="main_idea",
    )
    artifact = _make_artifact(
        "文章的主旨是讨论加拿大城市的发展历程。"  # No specific cities named
    )
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is True
    assert result.severity == "none"
    assert "recall not required" in result.details


def test_main_idea_default_flag_does_not_fail_on_missing_cities() -> None:
    """When ``requires_exhaustive_entity_recall`` is not set (default False),
    the dimension must NOT fail even if every entity is missing.
    """
    case = ReaderRecordAskCase(
        id="t-completeness-default",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="这篇文章的主旨是什么？",
        question_category="main_idea",
        expected=ReaderRecordAskExpected(
            expected_entity_set={"city": ["Thunder Bay", "Toronto"]}
            # requires_exhaustive_entity_recall NOT set → defaults to False
        ),
    )
    artifact = _make_artifact("文章的主旨是加拿大城市发展。")
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is True


def test_thunder_bay_alias_hits_chinese_translation() -> None:
    """Spec: "雷霆湾命中 Thunder Bay alias".

    The entity entry ``"Thunder Bay|雷霆湾|桑德贝"`` is considered
    present if ANY alias appears in the answer. ``雷霆湾`` must satisfy
    the entry even though the canonical English form is absent.
    """
    case = _make_case(
        {"city": ["Thunder Bay|雷霆湾|桑德贝", "Toronto|多伦多"]},
        requires_exhaustive=True,
    )
    # Only the Chinese aliases appear — must still pass.
    artifact = _make_artifact("文章提到了雷霆湾和多伦多。")
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is True


def test_genuinely_missing_city_still_fails_when_exhaustive() -> None:
    """Spec: "真正遗漏城市仍失败".

    When exhaustive=True and the answer genuinely omits an expected
    city (no alias appears), the dimension must fail.
    """
    case = _make_case(
        {"city": ["Thunder Bay|雷霆湾|桑德贝", "Toronto", "Vancouver"]},
        requires_exhaustive=True,
    )
    # Thunder Bay (and aliases) absent; Toronto and Vancouver present.
    artifact = _make_artifact("文章提到了 Toronto 和 Vancouver。")
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is False
    assert "Thunder Bay" in result.details  # the missing entry appears
    assert "recall=0.67" in result.details


def test_non_city_type_does_not_masquerade_as_city() -> None:
    """Spec: "非 city 类型不能冒充 city".

    A ``region`` entity set is checked under the ``region`` type —
    a missing ``region`` entity cannot be "satisfied" by mentioning
    a ``city`` entity. The type keys partition the recall check.
    """
    case = _make_case(
        {
            "city": ["Thunder Bay", "Toronto"],
            "region": ["安大略省", "魁北克省"],
        },
        requires_exhaustive=True,
    )
    # Cities present, regions absent → region recall fails.
    artifact = _make_artifact("文章提到了 Thunder Bay 和 Toronto。")
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is False
    assert "region" in result.details
    assert "安大略省" in result.details
    assert "魁北克省" in result.details
    # City recall succeeded — only region appears in the failure details.
    assert "city" not in result.details


def test_empty_entity_set_passes() -> None:
    case = _make_case({"city": []}, requires_exhaustive=True)
    artifact = _make_artifact("文章未提及具体城市。")
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is True


def test_multiple_types_mixed_recall_when_exhaustive() -> None:
    case = _make_case(
        {
            "city": ["Thunder Bay"],
            "region": ["安大略省", "魁北克省"],
        },
        requires_exhaustive=True,
    )
    artifact = _make_artifact("文章提到了 Thunder Bay。")  # missing both regions
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is False
    assert "region" in result.details
    assert "安大略省" in result.details
    assert "魁北克省" in result.details


# ---------------------------------------------------------------------------
# Legacy: explicit ``False`` flag should also pass vacuously
# ---------------------------------------------------------------------------


def test_explicit_false_flag_passes_vacuously() -> None:
    """Even when ``expected_entity_set`` is non-empty, explicit
    ``requires_exhaustive_entity_recall=False`` must NOT enforce recall.
    """
    case = _make_case(
        {"city": ["Thunder Bay", "Toronto", "Vancouver"]},
        requires_exhaustive=False,
    )
    artifact = _make_artifact("文章没有提到任何城市。")
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is True
    assert "recall not required" in result.details
