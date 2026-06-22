from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

VOCAB_DATASET_DIR = (
    Path(__file__).resolve().parents[1] / "datasets" / "vocabulary-seed-v1"
)
CANONICAL_BASELINE_PATH = (
    Path("baselines") / "vocabulary" / "vocabulary_baseline_2026_06.json"
)


def _run_baseline(tmp_path: Path) -> Path:
    output_path = tmp_path / "vocabulary_baseline_2026_06.json"
    cmd = [
        sys.executable,
        "scripts/run_vocabulary_baseline.py",
        "--dataset-dir",
        str(VOCAB_DATASET_DIR),
        "--runs-root",
        str(tmp_path / "runs"),
        "--output",
        str(output_path),
        "--run-id",
        "vocabulary-baseline-2026-06",
    ]
    subprocess.run(cmd, check=True, cwd=Path(__file__).resolve().parents[1])
    return output_path


def test_baseline_script_emits_summary_and_artifacts(tmp_path: Path) -> None:
    output_path = _run_baseline(tmp_path)
    summary = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["schema_kind"] == "vocabulary_seed_baseline"
    assert summary["dataset_id"] == "vocabulary-seed-v1"
    assert summary["dataset_dir"] == "datasets/vocabulary-seed-v1"
    assert summary["run_id"] == "vocabulary-baseline-2026-06"
    assert summary["failed"] == 0
    assert summary["total_cases"] >= 12
    assert "created_at" not in summary
    assert "case_summaries" not in summary
    assert summary["grader_names"] == [
        "anchor_resolution",
        "bounds_compliance",
        "diagnostics_coverage",
        "span_conflict_arb",
    ]
    run_dir = tmp_path / "runs" / "vocabulary-baseline-2026-06"
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "case-summaries.json").is_file()
    assert (run_dir / "run.json").is_file()


def test_baseline_persists_under_committed_baselines_directory() -> None:
    evals_root = Path(__file__).resolve().parents[1]
    canonical_baseline = evals_root / CANONICAL_BASELINE_PATH
    assert canonical_baseline.is_file(), (
        "Canonical baseline must be committed under evals/baselines/. "
        "Run scripts/run_vocabulary_baseline.py to produce it."
    )
    summary = json.loads(canonical_baseline.read_text(encoding="utf-8"))
    assert summary["schema_kind"] == "vocabulary_seed_baseline"
    assert summary["failed"] == 0
    assert summary["total_cases"] >= 12
    assert summary["dataset_id"] == "vocabulary-seed-v1"
    assert summary["dataset_dir"] == "datasets/vocabulary-seed-v1"
    assert "created_at" not in summary
