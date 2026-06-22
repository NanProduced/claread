from __future__ import annotations

from pathlib import Path

import pytest

from claread_eval.graders.vocabulary import (
    AnchorResolutionGrader,
    BoundsComplianceGrader,
    DiagnosticsCoverageGrader,
    SpanConflictArbitrationGrader,
    _fnv1a32_utf16,
    _slice_utf16,
    _utf16_code_units,
)
from claread_eval.loader.vocabulary_dataset_loader import (
    VocabularyDatasetLoadError,
    load_vocabulary_dataset,
)
from claread_eval.schemas.vocabulary import VocabularyEvalCase

VOCAB_DATASET_DIR = (
    Path(__file__).resolve().parents[1] / "datasets" / "vocabulary-seed-v1"
)


@pytest.fixture(scope="module")
def dataset() -> tuple[object, list[VocabularyEvalCase]]:
    return load_vocabulary_dataset(VOCAB_DATASET_DIR)


@pytest.fixture(scope="module")
def cases_by_id(dataset: tuple[object, list[VocabularyEvalCase]]) -> dict[str, VocabularyEvalCase]:
    _, cases = dataset
    return {case.id: case for case in cases}


def _run_all(case: VocabularyEvalCase) -> dict[str, object]:
    assert case.execution is not None, f"case={case.id} missing execution snapshot"
    graders = (
        AnchorResolutionGrader(),
        BoundsComplianceGrader(),
        DiagnosticsCoverageGrader(),
        SpanConflictArbitrationGrader(),
    )
    return {g.name: g.grade(case, case.execution) for g in graders}


def test_dataset_loads(dataset: tuple[object, list[VocabularyEvalCase]]) -> None:
    meta, cases = dataset
    assert meta.id == "vocabulary-seed-v1"
    assert len(cases) >= 12


def test_no_value_case_passes_all_graders(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-no-value"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )


def test_single_highlight_case_passes_all_graders(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-single-highlight"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )


def test_phrase_priority_case_keeps_phrase_gloss(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-phrase-priority"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )
    assert case.execution is not None
    assert [item.item_type for item in case.execution.output.items] == ["phrase_gloss"]


def test_context_priority_case_keeps_context_gloss(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-context-priority"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )
    assert case.execution is not None
    assert [item.item_type for item in case.execution.output.items] == ["context_gloss"]


def test_ambiguous_selected_text_is_skipped(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-ambiguous-selected-text"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )
    assert case.execution is not None
    assert case.execution.output.items == []
    reason_codes = [
        entry.get("reason_code")
        for entry in case.execution.diagnostics.get("skipped_items", [])
        if isinstance(entry, dict)
    ]
    assert "selected_text_ambiguous" in reason_codes


def test_not_found_case_reports_selected_text_not_found(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-not-found"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )


def test_unknown_segment_case_reports_anchor_segment_unknown(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-unknown-segment"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )


def test_same_span_conflict_resolves_to_context_gloss(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-same-span-conflict"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )
    assert case.execution is not None
    surviving_types = [item.item_type for item in case.execution.output.items]
    assert surviving_types == ["context_gloss"]
    reason_codes = [
        entry.get("reason_code")
        for entry in case.execution.diagnostics.get("skipped_items", [])
        if isinstance(entry, dict)
    ]
    assert reason_codes.count("span_conflict_higher_priority_kept") == 2


def test_span_conflict_grader_does_not_arbitrate_different_spans() -> None:
    unit_text = "The market rallied swiftly."
    market_start = _utf16_code_units("The ")
    market_end = market_start + _utf16_code_units("market")
    rallied_start = _utf16_code_units("The market ")
    rallied_end = rallied_start + _utf16_code_units("rallied")
    case = VocabularyEvalCase.model_validate(
        {
            "schema_version": 1,
            "id": "vocab-different-span-mixed-types",
            "unit_id": "u-mixed",
            "unit_text": unit_text,
            "anchor_segments": [
                {
                    "anchor_segment_id": "s1",
                    "sentence_id": "s1",
                    "segment_type": "sentence",
                    "unit_start_utf16": 0,
                    "unit_end_utf16": _utf16_code_units(unit_text),
                    "text": unit_text,
                    "boundary_quality": "normal",
                }
            ],
            "gold_items": [
                {
                    "item_type": "phrase_gloss",
                    "anchor_segment_id": "s1",
                    "selected_text": "market",
                    "phrase": "market",
                    "phrase_type": "proper_noun",
                    "gloss": "市场",
                },
                {
                    "item_type": "vocab_highlight",
                    "anchor_segment_id": "s1",
                    "selected_text": "rallied",
                    "headword": "rallied",
                    "brief_explanation": "反弹",
                },
            ],
            "execution": {
                "output": {
                    "schema_version": 1,
                    "items": [
                        {
                            "item_type": "phrase_gloss",
                            "anchor_segment_id": "s1",
                            "unit_start_utf16": market_start,
                            "unit_end_utf16": market_end,
                            "selected_text": "market",
                            "text_hash": _fnv1a32_utf16("market"),
                        },
                        {
                            "item_type": "vocab_highlight",
                            "anchor_segment_id": "s1",
                            "unit_start_utf16": rallied_start,
                            "unit_end_utf16": rallied_end,
                            "selected_text": "rallied",
                            "text_hash": _fnv1a32_utf16("rallied"),
                        },
                    ],
                },
                "diagnostics": {
                    "candidate_item_count": 2,
                    "resolved_item_count": 2,
                    "skipped_item_count": 0,
                    "skipped_items": [],
                    "skipped_items_truncated_count": 0,
                },
            },
        }
    )

    assert case.execution is not None
    result = SpanConflictArbitrationGrader().grade(case, case.execution)

    assert result.verdict == "pass", result.evidence


def test_diagnostics_exact_skipped_reason_codes_detect_mismatch(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-ambiguous-selected-text"].model_copy(deep=True)
    case.expected_diagnostics.skipped_reason_codes = ["selected_text_not_found"]

    assert case.execution is not None
    result = DiagnosticsCoverageGrader().grade(case, case.execution)

    assert result.verdict == "fail"
    assert "skipped_reason_codes mismatch" in result.evidence
    assert "selected_text_not_found" in result.evidence
    assert "selected_text_ambiguous" in result.evidence


def test_unicode_pitfall_reports_selected_text_not_found(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-unicode-pitfall"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )


def test_utf16_surrogate_offsets_round_trip(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-utf16-surrogate"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )
    assert case.execution is not None
    item = case.execution.output.items[0]
    slice_text = _slice_utf16(case.unit_text, item.unit_start_utf16, item.unit_end_utf16)
    assert slice_text == item.selected_text
    assert _utf16_code_units(item.selected_text) == 2  # emoji = 1 codepoint = 2 utf-16 units


def test_diagnostics_truncation_caps_at_eight(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-diagnostics-truncation"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )
    assert case.execution is not None
    diagnostics = case.execution.diagnostics
    assert len(diagnostics["skipped_items"]) == 8
    assert diagnostics["skipped_items_truncated_count"] == 2


def test_empty_with_diagnostics_passes(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-empty-with-diagnostics"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "pass", (
            f"grader={name} failed for case={case.id}: {result.evidence}"
        )


def test_too_many_candidates_fail_closed_skipped(
    cases_by_id: dict[str, VocabularyEvalCase],
) -> None:
    case = cases_by_id["vocab-too-many-fail-closed"]
    results = _run_all(case)
    for name, result in results.items():
        assert result.verdict == "skip", (
            f"grader={name} should skip for case={case.id}, got {result.verdict}"
        )


def test_fnv1a32_utf16_matches_known_value() -> None:
    # Worker uses FNV-1a 32-bit on UTF-16 code units (little-endian).
    # Independent implementation: hash("hello") over UTF-16 code units.
    # Reference value computed by app.contracts.annotation.compute_text_range_hash.
    assert _fnv1a32_utf16("hello") == "4f9f2cab"
    assert _fnv1a32_utf16("adjourned") == "b41fa5a3"


def test_loader_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(VocabularyDatasetLoadError, match="not found"):
        load_vocabulary_dataset(tmp_path / "does-not-exist")


def test_loader_rejects_missing_yaml(tmp_path: Path) -> None:
    empty = tmp_path / "empty-dataset"
    empty.mkdir()
    with pytest.raises(VocabularyDatasetLoadError, match="dataset.yaml not found"):
        load_vocabulary_dataset(empty)
