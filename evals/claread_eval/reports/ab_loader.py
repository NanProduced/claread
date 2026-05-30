from __future__ import annotations

from pathlib import Path

import orjson
from pydantic import BaseModel

from claread_eval.reports.ab_compare import AbReport, build_ab_report
from claread_eval.schemas.report import EvalReport
from claread_eval.schemas.run import EvalCaseArtifact
from claread_eval.writer.sanitizer import sanitized_payload


class AbReportLoadError(Exception):
    pass


class AbReportWriteError(Exception):
    pass


class LoadedRun(BaseModel):
    run_dir: Path
    run_id: str
    dataset_id: str | None = None
    report: EvalReport | None = None
    artifacts: list[EvalCaseArtifact]


def load_run_dir(run_dir: str | Path) -> LoadedRun:
    path = Path(run_dir)
    if not path.is_dir():
        raise AbReportLoadError(f"Run directory not found: {path}")

    run_json = _read_json(path / "run.json")
    run_id = run_json.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise AbReportLoadError(f"run.json missing run_id: {path / 'run.json'}")

    report = None
    report_path = path / "report.json"
    if report_path.is_file():
        report = EvalReport.model_validate(_read_json(report_path))

    artifacts = _load_case_artifacts(path / "cases", expected_run_id=run_id)
    dataset_id = run_json.get("dataset_id")
    return LoadedRun(
        run_dir=path,
        run_id=run_id,
        dataset_id=dataset_id if isinstance(dataset_id, str) else None,
        report=report,
        artifacts=artifacts,
    )


def build_ab_report_from_run_dirs(
    baseline_run_dir: str | Path,
    candidate_run_dir: str | Path,
) -> AbReport:
    baseline = load_run_dir(baseline_run_dir)
    candidate = load_run_dir(candidate_run_dir)
    report = build_ab_report(
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        baseline_dataset_id=baseline.dataset_id,
        candidate_dataset_id=candidate.dataset_id,
        baseline_artifacts=baseline.artifacts,
        candidate_artifacts=candidate.artifacts,
    )
    report.identity_warnings = [
        *_run_level_warnings(baseline, candidate),
        *report.identity_warnings,
    ]
    return report


def write_ab_report(
    report: AbReport,
    *,
    output_dir: str | Path,
    report_id: str | None = None,
) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = report_id or f"vs-{report.baseline_run_id}"
    json_path = output_dir / f"{filename}.json"
    md_path = output_dir / f"{filename}.md"
    if json_path.exists() or md_path.exists():
        raise AbReportWriteError(f"A/B report already exists: {json_path} / {md_path}")

    json_path.write_bytes(
        orjson.dumps(
            sanitized_payload(report.model_dump(mode="json")),
            option=orjson.OPT_INDENT_2,
        )
    )
    md_path.write_text(_render_ab_report_md(report), encoding="utf-8")
    return json_path, md_path


def write_ab_report_for_run_dirs(
    baseline_run_dir: str | Path,
    candidate_run_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    report_id: str | None = None,
) -> tuple[AbReport, tuple[Path, Path]]:
    candidate_path = Path(candidate_run_dir)
    report = build_ab_report_from_run_dirs(baseline_run_dir, candidate_path)
    paths = write_ab_report(
        report,
        output_dir=output_dir or candidate_path / "ab",
        report_id=report_id,
    )
    return report, paths


def _load_case_artifacts(cases_dir: Path, *, expected_run_id: str) -> list[EvalCaseArtifact]:
    if not cases_dir.is_dir():
        raise AbReportLoadError(f"Run cases directory not found: {cases_dir}")

    artifacts: list[EvalCaseArtifact] = []
    seen_case_ids: set[str] = set()
    for case_path in sorted(cases_dir.glob("*.json")):
        artifact = EvalCaseArtifact.model_validate(_read_json(case_path))
        if artifact.case_id in seen_case_ids:
            raise AbReportLoadError(f"Duplicate case_id in run artifacts: {artifact.case_id}")
        if case_path.stem != artifact.case_id:
            raise AbReportLoadError(
                f"Artifact case_id mismatch in {case_path}: {artifact.case_id}"
            )
        if artifact.run_id != expected_run_id:
            raise AbReportLoadError(
                f"Artifact run_id mismatch in {case_path}: "
                f"{artifact.run_id} != {expected_run_id}"
            )
        seen_case_ids.add(artifact.case_id)
        artifacts.append(artifact)

    if not artifacts:
        raise AbReportLoadError(f"No case artifacts found in {cases_dir}")
    return artifacts


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise AbReportLoadError(f"Required JSON file not found: {path}")
    try:
        data = orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError as exc:
        raise AbReportLoadError(f"Invalid JSON file: {path}") from exc
    if not isinstance(data, dict):
        raise AbReportLoadError(f"JSON file must contain an object: {path}")
    return data


def _run_level_warnings(baseline: LoadedRun, candidate: LoadedRun) -> list[str]:
    warnings: list[str] = []
    if baseline.dataset_id != candidate.dataset_id:
        warnings.append(
            f"hard warning: dataset_id differs: "
            f"{baseline.dataset_id or '<missing>'} -> {candidate.dataset_id or '<missing>'}"
        )
    if baseline.report and baseline.report.total_cases != len(baseline.artifacts):
        warnings.append(
            f"baseline report total_cases differs from artifacts: "
            f"{baseline.report.total_cases} != {len(baseline.artifacts)}"
        )
    if candidate.report and candidate.report.total_cases != len(candidate.artifacts):
        warnings.append(
            f"candidate report total_cases differs from artifacts: "
            f"{candidate.report.total_cases} != {len(candidate.artifacts)}"
        )
    return warnings


def _render_ab_report_md(report: AbReport) -> str:
    lines: list[str] = []
    lines.append(f"# A/B Report: {report.baseline_run_id} vs {report.candidate_run_id}")
    lines.append("")
    lines.append(f"- Created: {report.created_at.isoformat()}")
    lines.append(f"- Baseline dataset: `{report.baseline_dataset_id or '<missing>'}`")
    lines.append(f"- Candidate dataset: `{report.candidate_dataset_id or '<missing>'}`")
    lines.append(f"- Total paired cases: {report.total_cases}")
    lines.append(f"- Wins: {report.wins}")
    lines.append(f"- Losses: {report.losses}")
    lines.append(f"- Ties: {report.ties}")
    lines.append(f"- Manual review: {report.manual_review}")
    lines.append("")

    if report.identity_warnings:
        lines.append("## Identity Warnings")
        lines.append("")
        for warning in report.identity_warnings:
            lines.append(f"- {warning}")
        lines.append("")

    if report.regression_case_ids:
        lines.append("## Regression Cases")
        lines.append("")
        for case_id in report.regression_case_ids:
            lines.append(f"- `{case_id}`")
        lines.append("")

    lines.append("## Case Comparisons")
    lines.append("")
    lines.append("| Case ID | Verdict | Baseline Hard/Soft | Candidate Hard/Soft | Reasons |")
    lines.append("|---------|---------|--------------------|---------------------|---------|")
    for comparison in report.comparisons:
        reasons = "<br>".join(comparison.reasons)
        lines.append(
            f"| `{comparison.case_id}` | {comparison.verdict} | "
            f"{comparison.baseline_hard_failures}/{comparison.baseline_soft_failures} | "
            f"{comparison.candidate_hard_failures}/{comparison.candidate_soft_failures} | "
            f"{reasons} |"
        )
    lines.append("")
    return "\n".join(lines)
