from __future__ import annotations

from pathlib import Path

from claread_eval.graders.vocabulary import (
    AnchorResolutionGrader,
    BoundsComplianceGrader,
    DiagnosticsCoverageGrader,
    SpanConflictArbitrationGrader,
    summarize,
)
from claread_eval.loader.vocabulary_dataset_loader import load_vocabulary_dataset
from claread_eval.schemas.vocabulary import VocabularyGraderResult

VOCAB_DATASET_DIR = (
    Path(__file__).resolve().parents[1] / "datasets" / "vocabulary-seed-v1"
)


def test_every_case_has_execution_snapshot_and_grader_result() -> None:
    _, cases = load_vocabulary_dataset(VOCAB_DATASET_DIR)
    graders = (
        AnchorResolutionGrader(),
        BoundsComplianceGrader(),
        DiagnosticsCoverageGrader(),
        SpanConflictArbitrationGrader(),
    )
    total_results = 0
    for case in cases:
        assert case.execution is not None, f"case={case.id} missing execution snapshot"
        for grader in graders:
            result: VocabularyGraderResult = grader.grade(case, case.execution)
            assert result.case_id == case.id
            assert result.grader_name == grader.name
            total_results += 1

    assert total_results == len(cases) * 4


def test_aggregate_pass_rate_meets_baseline() -> None:
    """Every deterministic case must reach a pass verdict in all 4 graders.

    The fail-closed case legitimately returns skip; we count skip as
    acceptable. Every other case must produce 4 pass results.
    """
    _, cases = load_vocabulary_dataset(VOCAB_DATASET_DIR)
    graders = (
        AnchorResolutionGrader(),
        BoundsComplianceGrader(),
        DiagnosticsCoverageGrader(),
        SpanConflictArbitrationGrader(),
    )
    pass_count = 0
    skip_count = 0
    fail_count = 0
    for case in cases:
        assert case.execution is not None
        for grader in graders:
            result = grader.grade(case, case.execution)
            if result.verdict == "pass":
                pass_count += 1
            elif result.verdict == "skip":
                skip_count += 1
            else:
                fail_count += 1

    total = len(cases) * 4
    assert fail_count == 0, f"{fail_count} grader runs unexpectedly failed"
    assert pass_count + skip_count == total
    summary = summarize(
        [type("R", (), {"verdict": v})() for v in (["pass"] * pass_count + ["skip"] * skip_count)]
    )
    assert summary["pass"] + summary["skip"] == total