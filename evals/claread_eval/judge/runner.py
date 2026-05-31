from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from claread_eval.judge.adapters import create_judge_adapter, error_case_result
from claread_eval.judge.packet_builder import build_run_rubric_inputs
from claread_eval.schemas.judge import (
    JudgeCaseResult,
    JudgeCaseSummary,
    JudgeRunArtifact,
    JudgeRunReport,
)
from claread_eval.schemas.rubric import RubricSpec, load_rubric
from claread_eval.writer.sanitizer import sanitized_payload


class JudgeArtifactWriteError(Exception):
    pass


class JudgeRunError(Exception):
    pass


@dataclass
class JudgeRunConfig:
    judge_run_id: str
    run_id: str
    rubric_id: str
    rubric_version: str | None = None
    judge_adapter_kind: str = "fake"
    config_json: dict[str, Any] = field(default_factory=dict)
    max_cases: int | None = None
    source_text_char_limit: int = 1800
    output_item_limit: int = 12


async def run_judge(
    config: JudgeRunConfig,
    *,
    evals_root: str | Path,
    env: dict[str, str] | None = None,
) -> tuple[JudgeRunReport, Path]:
    evals_path = Path(evals_root).resolve()
    run_dir = evals_path / "runs" / config.run_id
    if not run_dir.is_dir():
        raise JudgeRunError(f"Eval run directory not found: {run_dir}")

    artifact_dir = run_dir / "judge" / config.judge_run_id
    if artifact_dir.exists():
        raise JudgeArtifactWriteError(
            f"Judge artifact directory already exists (immutable): {artifact_dir}"
        )

    rubric = _load_rubric(evals_path, config.rubric_id)
    if config.rubric_version and config.rubric_version != rubric.version:
        raise JudgeRunError(
            f"Rubric version mismatch: request={config.rubric_version}, file={rubric.version}"
        )

    adapter = create_judge_adapter(config.judge_adapter_kind, env=env)
    source_text_char_limit = _clamped_int(
        config.config_json.get("source_text_char_limit", config.source_text_char_limit),
        default=config.source_text_char_limit,
        minimum=200,
        maximum=8000,
    )
    output_item_limit = _clamped_int(
        config.config_json.get("output_item_limit", config.output_item_limit),
        default=config.output_item_limit,
        minimum=1,
        maximum=50,
    )
    max_cases = _optional_clamped_int(
        config.config_json.get("max_cases", config.max_cases),
        minimum=1,
        maximum=1000,
    )
    packets = build_run_rubric_inputs(
        rubric=rubric,
        run_dir=run_dir,
        source_text_char_limit=source_text_char_limit,
        output_item_limit=output_item_limit,
    )
    total_available_cases = len(packets)
    if max_cases is not None:
        packets = packets[:max_cases]

    artifact_dir.mkdir(parents=True, exist_ok=False)
    packets_dir = artifact_dir / "packets"
    packets_dir.mkdir()
    for packet in packets:
        _write_json(
            packets_dir / f"{packet.case_id}.json",
            sanitized_payload(packet.model_dump(mode="json"), mode="strip"),
        )

    run_artifact = JudgeRunArtifact(
        judge_run_id=config.judge_run_id,
        run_id=config.run_id,
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        judge_adapter_kind=adapter.adapter_kind,
        config_json={
            **config.config_json,
            "max_cases": max_cases,
            "source_text_char_limit": source_text_char_limit,
            "output_item_limit": output_item_limit,
        },
    )
    _write_json(
        artifact_dir / "judge-run.json",
        sanitized_payload(run_artifact.model_dump(mode="json"), mode="strip"),
    )

    results: list[JudgeCaseResult] = []
    for packet in packets:
        try:
            result = await adapter.judge_case(packet)
        except Exception as exc:
            result = error_case_result(
                packet=packet,
                adapter_kind=adapter.adapter_kind,
                exc=exc,
            )
        results.append(result)

    _write_json(
        artifact_dir / "case-results.json",
        sanitized_payload(
            {
                "schema_version": "eval-judge-case-results-v1",
                "judge_run_id": config.judge_run_id,
                "run_id": config.run_id,
                "generated_at": datetime.now(UTC).isoformat(),
                "cases": [result.model_dump(mode="json") for result in results],
            },
            mode="strip",
        ),
    )
    report = build_judge_report(
        judge_run_id=config.judge_run_id,
        run_id=config.run_id,
        rubric=rubric,
        judge_adapter_kind=adapter.adapter_kind,
        results=results,
        total_available_cases=total_available_cases,
        max_cases=max_cases,
    )
    _write_json(
        artifact_dir / "report.json",
        sanitized_payload(report.model_dump(mode="json"), mode="strip"),
    )
    (artifact_dir / "report.md").write_text(_render_report_md(report), encoding="utf-8")
    return report, artifact_dir


def build_judge_report(
    *,
    judge_run_id: str,
    run_id: str,
    rubric: RubricSpec,
    judge_adapter_kind: str,
    results: list[JudgeCaseResult],
    total_available_cases: int | None = None,
    max_cases: int | None = None,
) -> JudgeRunReport:
    scores = [result.overall_score for result in results if result.overall_score is not None]
    threshold = _rubric_threshold(rubric)
    low_score_case_ids = [
        result.case_id
        for result in results
        if result.overall_score is not None
        and threshold is not None
        and result.overall_score < threshold
    ]
    notes = [
        "LLM-as-a-Judge v1 is evidence only. "
        "Deterministic hard failures and human review remain authoritative."
    ]
    limited = (
        max_cases is not None
        and total_available_cases is not None
        and total_available_cases > len(results)
    )
    if limited:
        notes.append(
            f"Judge run was limited to {len(results)} of {total_available_cases} "
            "cases by max_cases."
        )
    return JudgeRunReport(
        judge_run_id=judge_run_id,
        run_id=run_id,
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        judge_adapter_kind=judge_adapter_kind,
        total_cases=len(results),
        passed=sum(1 for result in results if result.verdict == "pass"),
        failed=sum(1 for result in results if result.verdict == "fail"),
        needs_review=sum(1 for result in results if result.verdict == "needs_review"),
        errored=sum(1 for result in results if result.status == "error"),
        average_score=round(sum(scores) / len(scores), 4) if scores else None,
        low_score_case_ids=low_score_case_ids,
        case_summaries=[
            JudgeCaseSummary(
                case_id=result.case_id,
                status=result.status,
                verdict=result.verdict,
                overall_score=result.overall_score,
                error=result.error,
            )
            for result in results
        ],
        notes=notes,
    )


def _load_rubric(evals_root: Path, rubric_id: str) -> RubricSpec:
    rubric_path = evals_root / "rubrics" / f"{rubric_id}.yaml"
    if not rubric_path.is_file():
        alt_path = evals_root / "rubrics" / f"{rubric_id}.yml"
        if alt_path.is_file():
            rubric_path = alt_path
    return load_rubric(rubric_path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))


def _clamped_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    return min(parsed, maximum)


def _optional_clamped_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < minimum:
        return minimum
    return min(parsed, maximum)


def _rubric_threshold(rubric: RubricSpec) -> float | None:
    total_weight = sum(criterion.weight for criterion in rubric.criteria)
    if total_weight <= 0:
        return None
    weighted = sum(criterion.pass_score * criterion.weight for criterion in rubric.criteria)
    return weighted / total_weight


def _render_report_md(report: JudgeRunReport) -> str:
    lines = [
        f"# Judge Report: {report.judge_run_id}",
        "",
        f"- Run: `{report.run_id}`",
        f"- Rubric: `{report.rubric_id}@{report.rubric_version}`",
        f"- Adapter: `{report.judge_adapter_kind}`",
        f"- Created: {report.created_at.isoformat()}",
        f"- Total cases: {report.total_cases}",
        f"- Passed: {report.passed}",
        f"- Failed: {report.failed}",
        f"- Needs review: {report.needs_review}",
        f"- Errored: {report.errored}",
        f"- Average score: {report.average_score if report.average_score is not None else ''}",
        "",
        "> Judge v1 is evidence only. It does not override deterministic failures or human review.",
        "",
        "| Case ID | Verdict | Status | Score | Error |",
        "|---------|---------|--------|-------|-------|",
    ]
    for case in report.case_summaries:
        error = ""
        if case.error:
            error = str(case.error.get("message") or case.error.get("code") or "")[:80]
        score = "" if case.overall_score is None else str(case.overall_score)
        lines.append(
            f"| `{case.case_id}` | {case.verdict} | {case.status} | {score} | {error} |"
        )
    lines.append("")
    return "\n".join(lines)
