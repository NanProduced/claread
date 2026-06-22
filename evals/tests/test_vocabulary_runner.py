from __future__ import annotations

import json
from pathlib import Path

import pytest

from claread_eval.graders.vocabulary import (
    AnchorResolutionGrader,
    BoundsComplianceGrader,
)
from claread_eval.loader.vocabulary_dataset_loader import load_vocabulary_dataset
from claread_eval.runner.vocabulary_runner import (
    DEFAULT_GRADERS,
    VocabularyRunnerError,
    run_vocabulary_seed,
    summarize,
)
from claread_eval.schemas.vocabulary import VocabularyCaseSummary, VocabularySeedReport

VOCAB_DATASET_DIR = (
    Path(__file__).resolve().parents[1] / "datasets" / "vocabulary-seed-v1"
)


@pytest.fixture(scope="module")
def dataset() -> tuple[object, list]:
    return load_vocabulary_dataset(VOCAB_DATASET_DIR)


def _run_default(tmp_path: Path) -> VocabularySeedReport:
    return run_vocabulary_seed(
        dataset_dir=VOCAB_DATASET_DIR,
        run_id="unit-test-run",
        runs_root=tmp_path,
    )


def test_runner_returns_seed_report(
    tmp_path: Path, dataset: tuple[object, list]
) -> None:
    _, cases = dataset
    report = _run_default(tmp_path)
    assert isinstance(report, VocabularySeedReport)
    assert report.total_cases == len(cases)
    assert report.dataset_id == "vocabulary-seed-v1"
    assert report.grader_names == [g.name for g in DEFAULT_GRADERS]
    assert report.failed == 0


def test_runner_persists_artifacts(tmp_path: Path) -> None:
    run_vocabulary_seed(
        dataset_dir=VOCAB_DATASET_DIR,
        run_id="artifact-test-run",
        runs_root=tmp_path,
    )
    run_dir = tmp_path / "artifact-test-run"
    assert (run_dir / "case-summaries.json").is_file()
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "run.json").is_file()


def test_runner_run_json_records_metadata(tmp_path: Path) -> None:
    run_vocabulary_seed(
        dataset_dir=VOCAB_DATASET_DIR,
        run_id="meta-run",
        runs_root=tmp_path,
        workflow_version="test-workflow-v1",
        prompt_version="test-prompt-v1",
        note="fixture run",
    )
    run_snapshot = json.loads((tmp_path / "meta-run" / "run.json").read_text(encoding="utf-8"))
    assert run_snapshot["schema_kind"] == "vocabulary_seed_run"
    assert run_snapshot["workflow_version"] == "test-workflow-v1"
    assert run_snapshot["prompt_version"] == "test-prompt-v1"
    assert run_snapshot["note"] == "fixture run"
    assert run_snapshot["graders"] == [g.name for g in DEFAULT_GRADERS]


def test_runner_case_summary_per_case(
    tmp_path: Path, dataset: tuple[object, list]
) -> None:
    _, cases = dataset
    report = _run_default(tmp_path)
    summaries = {cs.case_id: cs for cs in report.case_summaries}
    assert set(summaries) == {c.id for c in cases}
    for case in cases:
        cs = summaries[case.id]
        assert isinstance(cs, VocabularyCaseSummary)
        assert len(cs.grader_results) == len(DEFAULT_GRADERS)
        assert cs.hard_failures == 0


def test_runner_pass_counts_match_default_graders(
    tmp_path: Path, dataset: tuple[object, list]
) -> None:
    _, cases = dataset
    report = _run_default(tmp_path)
    expected_total_pass = 0
    for case in cases:
        for grader in DEFAULT_GRADERS:
            result = grader.grade(case, case.execution)
            if result.verdict == "pass":
                expected_total_pass += 1
    actual_total_pass = sum(report.grader_pass_counts.values())
    assert actual_total_pass == expected_total_pass


def test_runner_rejects_missing_dataset_dir(tmp_path: Path) -> None:
    with pytest.raises(VocabularyRunnerError, match="not found"):
        run_vocabulary_seed(
            dataset_dir=tmp_path / "does-not-exist",
            run_id="bad-run",
            runs_root=tmp_path,
        )


def test_runner_accepts_subset_of_graders(
    tmp_path: Path, dataset: tuple[object, list]
) -> None:
    _, _ = dataset
    selected = (AnchorResolutionGrader(), BoundsComplianceGrader())
    report = run_vocabulary_seed(
        dataset_dir=VOCAB_DATASET_DIR,
        run_id="subset-graders",
        runs_root=tmp_path,
        graders=selected,
    )
    assert report.grader_names == ["anchor_resolution", "bounds_compliance"]
    assert report.failed == 0


def test_summarize_helper_runs_all_default_graders(
    dataset: tuple[object, list],
) -> None:
    _, cases = dataset
    case = cases[0]
    results = summarize(case, case.execution)
    assert {r.grader_name for r in results} == {g.name for g in DEFAULT_GRADERS}


def test_runner_writes_case_summaries_with_grader_payloads(tmp_path: Path) -> None:
    run_vocabulary_seed(
        dataset_dir=VOCAB_DATASET_DIR,
        run_id="payload-run",
        runs_root=tmp_path,
    )
    payload = json.loads(
        (tmp_path / "payload-run" / "case-summaries.json").read_text(encoding="utf-8")
    )
    assert isinstance(payload, list) and len(payload) >= 12
    first = payload[0]
    assert "grader_results" in first
    first_grader = first["grader_results"][0]
    for required_key in ("grader_name", "case_id", "verdict", "severity", "metric", "evidence"):
        assert required_key in first_grader