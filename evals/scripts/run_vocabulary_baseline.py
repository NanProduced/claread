"""Generate the deterministic vocabulary seed baseline run.

Run from `evals/`:

    uv run python scripts/run_vocabulary_baseline.py

Writes (default paths):
  - <runs-root>/vocabulary-baseline-2026-06/case-summaries.json
  - <runs-root>/vocabulary-baseline-2026-06/report.json
  - <runs-root>/vocabulary-baseline-2026-06/run.json
  - <output>  (defaults to evals/baselines/vocabulary/vocabulary_baseline_2026_06.json)

The script is intentionally synchronous and does not require any
network or third-party dependency.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claread_eval.runner.vocabulary_runner import run_vocabulary_seed
from claread_eval.schemas.vocabulary import VocabularySeedReport

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = EVALS_ROOT / "datasets" / "vocabulary-seed-v1"
DEFAULT_RUNS_ROOT = EVALS_ROOT / "runs"
DEFAULT_BASELINE_OUTPUT = (
    EVALS_ROOT / "baselines" / "vocabulary" / "vocabulary_baseline_2026_06.json"
)
BASELINE_PROMPT_VERSION = "vocabulary-baseline-2026-06"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        default=str(DEFAULT_DATASET_DIR),
        help="Path to the vocabulary seed dataset directory.",
    )
    parser.add_argument(
        "--runs-root",
        default=str(DEFAULT_RUNS_ROOT),
        help="Root directory under which per-run artifacts are persisted.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_BASELINE_OUTPUT),
        help="Path to write the flattened baseline JSON summary.",
    )
    parser.add_argument(
        "--run-id",
        default="vocabulary-baseline-2026-06",
        help="Per-run directory name under --runs-root.",
    )
    parser.add_argument(
        "--workflow-version",
        default="d5-v3-vocabulary-worker",
        help="Workflow version recorded in the run.json snapshot.",
    )
    args = parser.parse_args()

    report = run_vocabulary_seed(
        dataset_dir=args.dataset_dir,
        run_id=args.run_id,
        runs_root=args.runs_root,
        workflow_version=args.workflow_version,
        prompt_version=BASELINE_PROMPT_VERSION,
        note="D5 vocabulary eval baseline (deterministic)",
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_payload = _canonical_baseline_payload(
        report=report,
        dataset_dir=Path(args.dataset_dir),
        workflow_version=args.workflow_version,
    )
    output_path.write_text(
        json.dumps(baseline_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Wrote baseline run to {args.runs_root}/{args.run_id}/ and summary to {output_path}"
    )
    print(
        f"total_cases={report.total_cases} passed={report.passed} "
        f"failed={report.failed} skipped={report.skipped}"
    )


def _canonical_baseline_payload(
    *,
    report: VocabularySeedReport,
    dataset_dir: Path,
    workflow_version: str,
) -> dict[str, object]:
    relative_dataset_dir = _stable_evals_relative_path(dataset_dir)
    return {
        "schema_version": 1,
        "schema_kind": "vocabulary_seed_baseline",
        "run_id": report.run_id,
        "dataset_id": report.dataset_id,
        "dataset_dir": relative_dataset_dir,
        "workflow_version": workflow_version,
        "prompt_version": BASELINE_PROMPT_VERSION,
        "grader_names": report.grader_names,
        "total_cases": report.total_cases,
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
        "hard_failure_case_ids": report.hard_failure_case_ids,
        "soft_failure_case_ids": report.soft_failure_case_ids,
        "verdict_counts": report.verdict_counts,
        "grader_pass_counts": report.grader_pass_counts,
    }


def _stable_evals_relative_path(path: Path) -> str:
    resolved_path = path.resolve()
    try:
        return resolved_path.relative_to(EVALS_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    main()
