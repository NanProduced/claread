"""Local deterministic vocabulary seed runner.

This runner is intentionally separate from `simple_runner.run_eval` so
the vocabulary seed contract does not depend on the article-analysis
adapter / `EvalCaseArtifact` / `EvalReport` schema. The output is a
`VocabularySeedReport` written as JSON next to per-case summaries.

The runner does not call any LLM, does not touch LangSmith, and does
not require third-party dependencies beyond what the project already
declares.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from claread_eval.graders.vocabulary import (
    AnchorResolutionGrader,
    BoundsComplianceGrader,
    DiagnosticsCoverageGrader,
    SpanConflictArbitrationGrader,
    VocabularyGrader,
)
from claread_eval.loader.vocabulary_dataset_loader import (
    VocabularyDatasetLoadError,
    load_vocabulary_dataset,
)
from claread_eval.schemas.vocabulary import (
    VocabularyCaseSummary,
    VocabularyEvalCase,
    VocabularyExecutionSnapshot,
    VocabularyGraderResult,
    VocabularySeedReport,
    VocabularyVerdict,
)

DEFAULT_GRADERS: tuple[VocabularyGrader, ...] = (
    AnchorResolutionGrader(),
    BoundsComplianceGrader(),
    DiagnosticsCoverageGrader(),
    SpanConflictArbitrationGrader(),
)


class VocabularyRunnerError(Exception):
    """Raised when the vocabulary runner cannot complete a run."""


def run_vocabulary_seed(
    dataset_dir: str | Path,
    *,
    run_id: str,
    graders: Iterable[VocabularyGrader] = DEFAULT_GRADERS,
    runs_root: str | Path = "runs",
    prompt_version: str | None = None,
    workflow_version: str = "d5-v3-vocabulary-worker",
    note: str | None = None,
) -> VocabularySeedReport:
    """Run all vocabulary seed cases through the supplied graders.

    Persists artifacts under ``<runs_root>/<run_id>/``:
      - ``case-summaries.json`` (list[VocabularyCaseSummary])
      - ``report.json`` (VocabularySeedReport)
      - ``run.json`` (run config snapshot)

    Returns the in-memory report. The runner is synchronous and does
    not depend on any async adapter.
    """
    dataset_dir_path = Path(dataset_dir)
    if not dataset_dir_path.is_dir():
        raise VocabularyRunnerError(
            f"Vocabulary dataset directory not found: {dataset_dir_path}"
        )

    try:
        dataset, cases = load_vocabulary_dataset(dataset_dir_path)
    except VocabularyDatasetLoadError as exc:
        raise VocabularyRunnerError(str(exc)) from exc

    grader_list = list(graders)
    grader_names = [g.name for g in grader_list]
    run_dir = Path(runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    case_summaries: list[VocabularyCaseSummary] = []
    all_results: list[VocabularyGraderResult] = []
    hard_failure_ids: list[str] = []
    soft_failure_ids: list[str] = []
    passed_count = 0
    failed_count = 0
    skipped_count = 0
    verdict_counts: Counter[str] = Counter()
    grader_pass_counts: Counter[str] = Counter()

    for case in cases:
        if case.execution is None:
            raise VocabularyRunnerError(
                f"Case {case.id!r} is missing the deterministic execution snapshot"
            )
        snapshot = case.execution
        case_results: list[VocabularyGraderResult] = []
        case_hard_failures = 0
        case_soft_failures = 0
        case_skip = 0
        for grader in grader_list:
            result = grader.grade(case, snapshot)
            case_results.append(result)
            all_results.append(result)
            verdict_counts[result.verdict] += 1
            if result.verdict == "pass":
                grader_pass_counts[grader.name] += 1
            if result.verdict == "fail":
                if result.severity == "hard":
                    case_hard_failures += 1
                elif result.severity == "soft":
                    case_soft_failures += 1
            elif result.verdict == "skip":
                case_skip += 1

        if case_hard_failures > 0:
            case_verdict: VocabularyVerdict = "fail"
            hard_failure_ids.append(case.id)
            failed_count += 1
        elif case_soft_failures > 0:
            case_verdict = "fail"
            soft_failure_ids.append(case.id)
            failed_count += 1
        elif case_skip > 0 and case_skip == len(grader_list):
            case_verdict = "skip"
            skipped_count += 1
        else:
            case_verdict = "pass"
            passed_count += 1

        case_summaries.append(
            VocabularyCaseSummary(
                case_id=case.id,
                case_verdict=case_verdict,
                hard_failures=case_hard_failures,
                soft_failures=case_soft_failures,
                skip_count=case_skip,
                grader_results=case_results,
            )
        )

    report = VocabularySeedReport(
        run_id=run_id,
        dataset_id=dataset.id,
        dataset_dir=str(dataset_dir_path),
        grader_names=grader_names,
        total_cases=len(cases),
        passed=passed_count,
        failed=failed_count,
        skipped=skipped_count,
        hard_failure_case_ids=hard_failure_ids,
        soft_failure_case_ids=soft_failure_ids,
        verdict_counts=dict(verdict_counts),
        grader_pass_counts=dict(grader_pass_counts),
        case_summaries=case_summaries,
    )

    _write_artifacts(
        run_dir=run_dir,
        report=report,
        run_id=run_id,
        dataset_id=dataset.id,
        dataset_dir=str(dataset_dir_path),
        prompt_version=prompt_version,
        workflow_version=workflow_version,
        note=note,
    )

    return report


def _write_artifacts(
    *,
    run_dir: Path,
    report: VocabularySeedReport,
    run_id: str,
    dataset_id: str,
    dataset_dir: str,
    prompt_version: str | None,
    workflow_version: str,
    note: str | None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    case_payload = [cs.model_dump(mode="json") for cs in report.case_summaries]
    (run_dir / "case-summaries.json").write_text(
        json.dumps(case_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    run_snapshot = {
        "schema_kind": "vocabulary_seed_run",
        "run_id": run_id,
        "dataset_id": dataset_id,
        "dataset_dir": dataset_dir,
        "workflow_version": workflow_version,
        "prompt_version": prompt_version,
        "note": note,
        "graders": report.grader_names,
        "total_cases": report.total_cases,
    }
    (run_dir / "run.json").write_text(
        json.dumps(run_snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def summarize(
    case: VocabularyEvalCase,
    snapshot: VocabularyExecutionSnapshot,
    graders: Iterable[VocabularyGrader] = DEFAULT_GRADERS,
) -> list[VocabularyGraderResult]:
    """Convenience helper: run all graders on a single case snapshot."""
    return [grader.grade(case, snapshot) for grader in graders]