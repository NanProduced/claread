from __future__ import annotations

from pathlib import Path

import orjson

from claread_eval.schemas.report import EvalReport
from claread_eval.schemas.run import EvalCaseArtifact, EvalRunConfig
from claread_eval.writer.sanitizer import sanitized_artifact_payload, sanitized_payload


class ArtifactWriteError(Exception):
    pass


def _write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = orjson.dumps(data, option=orjson.OPT_INDENT_2)
    path.write_bytes(raw)


def _model_to_dict(obj: object) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"Cannot serialize {type(obj)}")


def init_run_dir(runs_root: str | Path, run_config: EvalRunConfig) -> Path:
    runs_root = Path(runs_root)
    run_dir = runs_root / run_config.run_id
    if run_dir.exists():
        raise ArtifactWriteError(
            f"Run directory already exists (immutable): {run_dir}"
        )
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "cases").mkdir()
    _write_json(run_dir / "run.json", sanitized_payload(_model_to_dict(run_config)))
    return run_dir


def write_case_artifact(run_dir: str | Path, artifact: EvalCaseArtifact) -> Path:
    run_dir = Path(run_dir)
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    case_path = cases_dir / f"{artifact.case_id}.json"
    if case_path.exists():
        raise ArtifactWriteError(
            f"Case artifact already exists (immutable): {case_path}"
        )
    _write_json(case_path, sanitized_artifact_payload(artifact))
    return case_path


def write_report(run_dir: str | Path, report: EvalReport) -> tuple[Path, Path]:
    run_dir = Path(run_dir)
    json_path = run_dir / "report.json"
    md_path = run_dir / "report.md"

    _write_json(json_path, _model_to_dict(report))

    md_content = _render_report_md(report)
    md_path.write_text(md_content, encoding="utf-8")

    return json_path, md_path


def _render_report_md(report: EvalReport) -> str:
    lines: list[str] = []
    lines.append(f"# Eval Report: {report.run_id}")
    lines.append("")
    lines.append(f"- Dataset: `{report.dataset_id}`")
    lines.append(f"- Created: {report.created_at.isoformat()}")
    lines.append(f"- Total cases: {report.total_cases}")
    lines.append(f"- Passed: {report.passed}")
    lines.append(f"- Failed: {report.failed}")
    lines.append(f"- Skipped: {report.skipped}")
    lines.append(f"- Errored: {report.errored}")
    lines.append("")

    if report.hard_failure_case_ids:
        lines.append("## Hard Failures")
        lines.append("")
        for cid in report.hard_failure_case_ids:
            lines.append(f"- `{cid}`")
        lines.append("")

    if report.soft_failure_case_ids:
        lines.append("## Soft Failures")
        lines.append("")
        for cid in report.soft_failure_case_ids:
            lines.append(f"- `{cid}`")
        lines.append("")

    if report.case_summaries:
        lines.append("## Case Summaries")
        lines.append("")
        lines.append("| Case ID | Verdict | Hard | Soft | Error |")
        lines.append("|---------|---------|------|------|-------|")
        for cs in report.case_summaries:
            err_display = cs.error or ""
            if len(err_display) > 40:
                err_display = err_display[:37] + "..."
            lines.append(
                f"| `{cs.case_id}` | {cs.verdict.value} | {cs.hard_failures} "
                f"| {cs.soft_failures} | {err_display} |"
            )
        lines.append("")

    if report.regression_list:
        lines.append("## Regressions")
        lines.append("")
        for r in report.regression_list:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines)
