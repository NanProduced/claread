from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.exhaustive_completeness import (
    evaluate_exhaustive_completeness,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)


def _make_case(entity_set: dict[str, list[str]]) -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id="t-completeness",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category="city_enumeration",
        expected=ReaderRecordAskR4A3Expected(expected_entity_set=entity_set),
    )


def _make_artifact(final_text: str) -> RawArtifact:
    return RawArtifact(
        case_id="t-completeness",
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
    )


def test_positive_all_entities_present() -> None:
    case = _make_case(
        {"city": ["Thunder Bay", "Toronto", "Vancouver", "Montreal", "Ottawa"]}
    )
    artifact = _make_artifact(
        "文章提到的城市包括 Thunder Bay、Toronto、Vancouver、Montreal 和 Ottawa。"
    )
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_negative_missing_thunder_bay_recall_below_one() -> None:
    # 5 expected, 1 (Thunder Bay) missing → recall=0.8
    case = _make_case(
        {"city": ["Thunder Bay", "Toronto", "Vancouver", "Montreal", "Ottawa"]}
    )
    artifact = _make_artifact(
        "文章提到的城市包括 Toronto、Vancouver、Montreal 和 Ottawa。"
    )
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "Thunder Bay" in result.details
    assert "recall=0.80" in result.details


def test_negative_multiple_types_missing() -> None:
    case = _make_case(
        {
            "city": ["Thunder Bay"],
            "region": ["安大略省", "魁北克省"],
        }
    )
    artifact = _make_artifact("文章提到了 Thunder Bay。")  # missing both regions
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is False
    assert "region" in result.details
    assert "安大略省" in result.details
    assert "魁北克省" in result.details


def test_empty_entity_set_passes() -> None:
    case = _make_case({"city": []})
    artifact = _make_artifact("文章未提及具体城市。")
    result = evaluate_exhaustive_completeness(case, artifact)
    assert result.passed is True
