from __future__ import annotations

import json
from pathlib import Path

import pytest

from claread_eval.reports.ab_compare import (
    build_ab_report,
    compare_case_artifacts,
)
from claread_eval.reports.ab_loader import (
    AbReportLoadError,
    AbReportWriteError,
    build_ab_report_from_run_dirs,
    write_ab_report,
    write_ab_report_for_run_dirs,
)
from claread_eval.schemas.report import CaseSummary, EvalReport
from claread_eval.schemas.run import EvalCaseArtifact, EvalRunConfig
from claread_eval.writer.artifact_writer import init_run_dir, write_case_artifact, write_report


def _artifact(
    *,
    run_id: str,
    case_id: str = "case-1",
    hard_failures: int = 0,
    soft_failures: int = 0,
    status: str = "succeeded",
    prompt_version: str | None = "prompt-a",
    model_name: str | None = "model-a",
) -> EvalCaseArtifact:
    grader_results = [
        {"verdict": "fail", "severity": "hard"}
        for _ in range(hard_failures)
    ]
    grader_results.extend(
        {"verdict": "fail", "severity": "soft"}
        for _ in range(soft_failures)
    )
    return EvalCaseArtifact(
        case_id=case_id,
        run_id=run_id,
        adapter_status=status,
        workflow_identity={
            "workflow_name": "article_analysis",
            "workflow_version": "3.0.0",
            "topology_mode": "learning",
        },
        schema_identity={
            "schema_version": "3.0.0",
            "render_schema_version": "3.0.0",
            "topology_mode": "learning",
        },
        prompt_identity={"prompt_version": prompt_version},
        model_identity={
            "route": "annotation_generation",
            "model_name": model_name,
        },
        grader_results=grader_results,
        timeout=status == "timeout",
        error={"code": "TimeoutError", "message": "Timed out"}
        if status == "timeout"
        else None,
    )


def test_compare_case_artifacts_marks_candidate_loss_on_new_hard_failure() -> None:
    comparison = compare_case_artifacts(
        _artifact(run_id="baseline", hard_failures=0),
        _artifact(run_id="candidate", hard_failures=1),
    )
    assert comparison.verdict == "loss"
    assert comparison.case_id == "case-1"


def test_compare_case_artifacts_marks_candidate_win_on_fixed_failure() -> None:
    comparison = compare_case_artifacts(
        _artifact(run_id="baseline", hard_failures=1),
        _artifact(run_id="candidate", hard_failures=0),
    )
    assert comparison.verdict == "win"


def test_build_ab_report_pairs_shared_cases_only() -> None:
    report = build_ab_report(
        baseline_run_id="baseline",
        candidate_run_id="candidate",
        baseline_artifacts=[
            _artifact(run_id="baseline", case_id="case-1"),
            _artifact(run_id="baseline", case_id="case-2"),
        ],
        candidate_artifacts=[
            _artifact(run_id="candidate", case_id="case-1", hard_failures=1),
            _artifact(run_id="candidate", case_id="case-3"),
        ],
    )

    assert report.total_cases == 1
    assert report.losses == 1
    assert report.regression_case_ids == ["case-1"]
    assert any("baseline-only" in warning for warning in report.identity_warnings)
    assert any("candidate-only" in warning for warning in report.identity_warnings)


def test_build_ab_report_records_identity_warnings() -> None:
    report = build_ab_report(
        baseline_run_id="baseline",
        candidate_run_id="candidate",
        baseline_artifacts=[
            _artifact(run_id="baseline", prompt_version="prompt-a", model_name="model-a"),
        ],
        candidate_artifacts=[
            _artifact(run_id="candidate", prompt_version="prompt-a", model_name="model-b"),
        ],
    )

    assert any("prompt_identity is identical" in warning for warning in report.identity_warnings)
    assert any("model_identity differs" in warning for warning in report.identity_warnings)
    assert report.comparisons[0].identity_delta is not None
    assert "model_identity" in report.comparisons[0].identity_delta


def _write_run(
    runs_root: Path,
    *,
    run_id: str,
    dataset_id: str = "dataset-a",
    artifacts: list[EvalCaseArtifact],
    report_total_cases: int | None = None,
) -> Path:
    run_dir = init_run_dir(runs_root, EvalRunConfig(run_id=run_id, dataset_id=dataset_id))
    for artifact in artifacts:
        write_case_artifact(run_dir, artifact)
    total_cases = report_total_cases if report_total_cases is not None else len(artifacts)
    report = EvalReport(
        run_id=run_id,
        dataset_id=dataset_id,
        total_cases=total_cases,
        passed=total_cases,
        case_summaries=[
            CaseSummary(case_id=artifact.case_id)
            for artifact in artifacts
        ],
    )
    write_report(run_dir, report)
    return run_dir


def test_build_ab_report_from_run_dirs(tmp_path: Path) -> None:
    baseline_dir = _write_run(
        tmp_path,
        run_id="baseline",
        artifacts=[
            _artifact(run_id="baseline", case_id="case-1"),
            _artifact(run_id="baseline", case_id="case-2", hard_failures=1),
        ],
    )
    candidate_dir = _write_run(
        tmp_path,
        run_id="candidate",
        artifacts=[
            _artifact(run_id="candidate", case_id="case-1", hard_failures=1),
            _artifact(run_id="candidate", case_id="case-2"),
        ],
    )

    report = build_ab_report_from_run_dirs(baseline_dir, candidate_dir)

    assert report.baseline_run_id == "baseline"
    assert report.candidate_run_id == "candidate"
    assert report.baseline_dataset_id == "dataset-a"
    assert report.candidate_dataset_id == "dataset-a"
    assert report.total_cases == 2
    assert report.wins == 1
    assert report.losses == 1
    assert report.regression_case_ids == ["case-1"]


def test_build_ab_report_from_run_dirs_records_run_level_warnings(tmp_path: Path) -> None:
    baseline_dir = _write_run(
        tmp_path,
        run_id="baseline",
        dataset_id="dataset-a",
        artifacts=[_artifact(run_id="baseline", case_id="case-1")],
        report_total_cases=2,
    )
    candidate_dir = _write_run(
        tmp_path,
        run_id="candidate",
        dataset_id="dataset-b",
        artifacts=[_artifact(run_id="candidate", case_id="case-1")],
    )

    report = build_ab_report_from_run_dirs(baseline_dir, candidate_dir)

    assert report.baseline_dataset_id == "dataset-a"
    assert report.candidate_dataset_id == "dataset-b"
    assert any(
        "hard warning: dataset_id differs" in warning
        for warning in report.identity_warnings
    )
    assert any(
        "baseline report total_cases differs" in warning
        for warning in report.identity_warnings
    )


def test_write_ab_report_is_immutable(tmp_path: Path) -> None:
    report = build_ab_report(
        baseline_run_id="baseline",
        candidate_run_id="candidate",
        baseline_artifacts=[_artifact(run_id="baseline")],
        candidate_artifacts=[_artifact(run_id="candidate")],
    )

    json_path, md_path = write_ab_report(report, output_dir=tmp_path)

    assert json_path.is_file()
    assert md_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["baseline_run_id"] == "baseline"
    assert json_path == tmp_path / "vs-baseline.json"
    assert "# A/B Report: baseline vs candidate" in md_path.read_text(encoding="utf-8")
    with pytest.raises(AbReportWriteError, match="already exists"):
        write_ab_report(report, output_dir=tmp_path)


def test_write_ab_report_for_run_dirs_uses_default_output_root(tmp_path: Path) -> None:
    baseline_dir = _write_run(
        tmp_path,
        run_id="baseline",
        artifacts=[_artifact(run_id="baseline", case_id="case-1")],
    )
    candidate_dir = _write_run(
        tmp_path,
        run_id="candidate",
        artifacts=[_artifact(run_id="candidate", case_id="case-1")],
    )

    report, (json_path, md_path) = write_ab_report_for_run_dirs(baseline_dir, candidate_dir)

    assert report.total_cases == 1
    assert json_path == candidate_dir / "ab" / "vs-baseline.json"
    assert md_path.is_file()


def test_build_ab_report_from_run_dirs_rejects_missing_cases_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "baseline"
    run_dir.mkdir()
    (run_dir / "run.json").write_text('{"run_id":"baseline","dataset_id":"dataset"}')
    candidate_dir = _write_run(
        tmp_path,
        run_id="candidate",
        artifacts=[_artifact(run_id="candidate", case_id="case-1")],
    )

    with pytest.raises(AbReportLoadError, match="cases directory"):
        build_ab_report_from_run_dirs(run_dir, candidate_dir)


def test_build_ab_report_from_run_dirs_rejects_case_filename_mismatch(tmp_path: Path) -> None:
    baseline_dir = _write_run(
        tmp_path,
        run_id="baseline",
        artifacts=[_artifact(run_id="baseline", case_id="case-1")],
    )
    candidate_dir = _write_run(
        tmp_path,
        run_id="candidate",
        artifacts=[_artifact(run_id="candidate", case_id="case-1")],
    )
    original = candidate_dir / "cases" / "case-1.json"
    mismatch = candidate_dir / "cases" / "other-case.json"
    original.rename(mismatch)

    with pytest.raises(AbReportLoadError, match="case_id mismatch"):
        build_ab_report_from_run_dirs(baseline_dir, candidate_dir)
