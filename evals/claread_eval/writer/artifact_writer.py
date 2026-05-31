from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from claread_eval.schemas.report import EvalReport
from claread_eval.schemas.run import EvalCaseArtifact, EvalRunConfig
from claread_eval.writer.sanitizer import sanitized_artifact_payload, sanitized_payload

CASE_INDEX_SCHEMA_VERSION = "eval-case-index-v1"


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


def build_case_index_entry(artifact: EvalCaseArtifact) -> dict[str, Any]:
    payload = sanitized_artifact_payload(artifact)
    grader_results = payload.get("grader_results", [])
    if not isinstance(grader_results, list):
        grader_results = []

    failed_graders = [
        result
        for result in grader_results
        if isinstance(result, dict) and result.get("verdict") == "fail"
    ]
    hard_failures = sum(1 for result in failed_graders if result.get("severity") == "hard")
    soft_failures = sum(1 for result in failed_graders if result.get("severity") == "soft")

    usage_summary = payload.get("usage_summary") or {}
    if not isinstance(usage_summary, dict):
        usage_summary = {}

    return {
        "case_id": payload.get("case_id"),
        "run_id": payload.get("run_id"),
        "artifact_href": f"cases/{payload.get('case_id')}.json",
        "adapter_status": payload.get("adapter_status"),
        "user_facing_state": payload.get("user_facing_state"),
        "error": _summarize_error(payload.get("error")),
        "warning_count": _list_count(payload.get("warnings")),
        "drop_count": _list_count(payload.get("drop_log")),
        "hard_failures": hard_failures,
        "soft_failures": soft_failures,
        "grader_count": len(grader_results),
        "failed_grader_count": len(failed_graders),
        "grader_summaries": [
            _summarize_grader_result(result)
            for result in grader_results
            if isinstance(result, dict)
        ],
        "translation_count": _list_count(payload.get("translations")),
        "inline_mark_count": _list_count(payload.get("inline_marks")),
        "sentence_entry_count": _list_count(payload.get("sentence_entries")),
        "latency_seconds": payload.get("latency_seconds"),
        "total_tokens": usage_summary.get("total_tokens"),
        "input_tokens": usage_summary.get("input_tokens"),
        "output_tokens": usage_summary.get("output_tokens"),
        "workflow_identity": payload.get("workflow_identity"),
        "schema_identity": payload.get("schema_identity"),
        "prompt_identity": payload.get("prompt_identity"),
        "model_identity": payload.get("model_identity"),
    }


def write_case_index(
    run_dir: str | Path,
    run_config: EvalRunConfig,
    artifacts: list[EvalCaseArtifact],
    *,
    overwrite: bool = False,
) -> Path:
    run_dir = Path(run_dir)
    index_path = run_dir / "case-index.json"
    if index_path.exists() and not overwrite:
        raise ArtifactWriteError(
            f"Case index already exists (immutable): {index_path}"
        )

    entries = [build_case_index_entry(artifact) for artifact in artifacts]
    payload = {
        "schema_version": CASE_INDEX_SCHEMA_VERSION,
        "run_id": run_config.run_id,
        "dataset_id": run_config.dataset_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_cases": len(entries),
        "cases": entries,
    }
    _write_json(index_path, sanitized_payload(payload))
    return index_path


def write_report(run_dir: str | Path, report: EvalReport) -> tuple[Path, Path]:
    run_dir = Path(run_dir)
    json_path = run_dir / "report.json"
    md_path = run_dir / "report.md"

    _write_json(json_path, _model_to_dict(report))

    md_content = _render_report_md(report)
    md_path.write_text(md_content, encoding="utf-8")

    return json_path, md_path


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _summarize_error(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "code": value.get("code"),
        "message": value.get("message"),
    }


def _summarize_grader_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "grader_name": result.get("grader_name"),
        "verdict": result.get("verdict"),
        "severity": result.get("severity"),
        "metric": result.get("metric"),
        "evidence": (
            result.get("evidence")
            or result.get("reason")
            or result.get("message")
        ),
    }


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
