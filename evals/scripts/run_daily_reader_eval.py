"""Daily Reader regression eval runner (task pack A-6).

One command to score daily-reader close-reading artifacts against
``evals/rubrics/daily-reader-regression-v1.yaml`` (deterministic gates
+ optional LLM judge), writing results under ``evals/runs/``.

Usage (run from ``evals/``, uses ``evals/.venv`` via uv):

    # Baseline: score the ALREADY-STORED daily_readers rows (no workflow run).
    uv run python scripts/run_daily_reader_eval.py --mode baseline

    # Full: re-run the production daily_reader workflow per case (paid LLM
    # calls, gated) and score the fresh artifacts. DB is never written.
    CLAREAD_ALLOW_REAL_LLM_TESTS=1 uv run python scripts/run_daily_reader_eval.py --mode workflow

    # Subset + judge:
    CLAREAD_ALLOW_REAL_LLM_TESTS=1 DEEPSEEK_API_KEY=... \
        uv run python scripts/run_daily_reader_eval.py --mode baseline \
        --case bbc-manifestos-002 --run-id my-comparison

Baseline mode needs docker (``docker exec claread-postgres psql ...``,
compose default credentials). See evals/README.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import yaml

from claread_eval.daily_reader.checks import run_deterministic_checks
from claread_eval.daily_reader.judge import judge_mean_score, run_judge

EVALS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EVALS_ROOT.parent
API_DIR = REPO_ROOT / "services" / "api"
DEFAULT_DATASET_DIR = EVALS_ROOT / "datasets" / "daily-reader-regression-v1"
RUBRIC_PATH = EVALS_ROOT / "rubrics" / "daily-reader-regression-v1.yaml"
GATE_ENV = "CLAREAD_ALLOW_REAL_LLM_TESTS"

BASELINE_COLUMNS = (
    "id, title, difficulty, original_text, body_json, highlights_json, "
    "paragraph_notes_json, takeaways_json"
)


def load_cases(dataset_dir: Path, case_ids: list[str]) -> list[dict]:
    cases = []
    for p in sorted((dataset_dir / "cases").glob("*.json")):
        case = json.loads(p.read_text(encoding="utf-8"))
        if not case_ids or case["case_id"] in case_ids:
            cases.append(case)
    unknown = set(case_ids) - {c["case_id"] for c in cases}
    if unknown:
        sys.exit(f"unknown case id(s): {sorted(unknown)}")
    if not cases:
        sys.exit("no cases selected")
    return cases


def fetch_baseline_row(daily_reader_id: str) -> dict | None:
    """Fetch one stored row via docker psql (compose default credentials)."""
    safe_id = daily_reader_id.replace("'", "''")
    sql = (f"SELECT row_to_json(t) FROM (SELECT {BASELINE_COLUMNS} FROM daily_readers "
           f"WHERE id = '{safe_id}') t;")
    proc = subprocess.run(
        ["docker", "exec", "claread-postgres", "psql", "-U", "claread", "-d", "claread",
         "-t", "-A", "-c", sql],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker psql failed: {proc.stderr.strip()[:300]}")
    line = proc.stdout.strip()
    if not line:
        return None
    row = json.loads(line)
    return {
        "case_id": None,
        "title": row["title"],
        "difficulty": row["difficulty"],
        "original_text": row["original_text"],
        "body_json": row["body_json"],
        "highlights_json": row["highlights_json"],
        "paragraph_notes_json": row["paragraph_notes_json"],
        "takeaways_json": row["takeaways_json"],
        "daily_reader_id": row["id"],
        "abort": False,
    }


def api_venv_python() -> Path:
    for rel in ("Scripts/python.exe", "bin/python"):
        p = API_DIR / ".venv" / rel
        if p.exists():
            return p
    sys.exit("services/api/.venv not found — run `uv sync` inside services/api first")


def run_workflow_harness(case_path: Path, out_path: Path) -> None:
    harness = EVALS_ROOT / "scripts" / "daily_reader_workflow_harness.py"
    proc = subprocess.run(
        [str(api_venv_python()), str(harness), str(case_path), str(out_path)],
        cwd=API_DIR, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"workflow harness failed:\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
        )


def score_case(rubric: dict, case: dict, artifact: dict, skip_judge: bool = False) -> dict:
    det = run_deterministic_checks(case, artifact)
    if artifact.get("abort"):
        judge = {"status": "skipped", "reason": "workflow aborted — nothing to judge"}
    elif skip_judge:
        judge = {"status": "skipped", "reason": "--no-judge"}
    else:
        judge = run_judge(rubric, case, artifact)
    judge_mean = judge_mean_score(judge)
    det_ratio = det["pass_ratio"]
    if judge_mean is not None:
        overall = round(0.5 * det_ratio + 0.5 * (judge_mean / 5.0), 4)
    else:
        overall = round(det_ratio, 4)
    return {"deterministic": det, "judge": judge,
            "judge_mean": round(judge_mean, 3) if judge_mean is not None else None,
            "overall": overall,
            "overall_basis": "det+judge" if judge_mean is not None else "deterministic_only"}


def render_report_md(run: dict) -> str:
    lines = [
        f"# Daily Reader 回归评测报告 — {run['run_id']}",
        "",
        f"- mode: `{run['mode']}` · dataset: `{run['dataset_id']}` · rubric: `{run['rubric_id']}`",
        f"- 时间: {run['created_at']} · judge: {run['judge_status']}",
        "",
        "| case | difficulty | no_boilerplate | highlight_dedup | translation_consistency "
        "| expr_coverage | 确定性 | judge均分 | overall |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cr in run["cases"]:
        det = cr["deterministic"]["checks"]
        mark = lambda ok: "✅" if ok is True else ("n/a" if ok is None else "❌")  # noqa: E731
        judge_mean = cr["judge_mean"] if cr["judge_mean"] is not None else "—"
        lines.append(
            f"| {cr['case_id']} | {cr['expected_difficulty']} "
            f"| {mark(det['no_boilerplate']['passed'])} "
            f"| {mark(det['highlight_dedup']['passed'])} "
            f"| {mark(det['translation_consistency']['passed'])} "
            f"| {mark(det['gold_expression_coverage']['passed'])} "
            f"| {cr['deterministic']['passed']}/{cr['deterministic']['total']} "
            f"| {judge_mean} | **{cr['overall']}** |"
        )
    lines += ["", f"总均分: **{run['aggregate']['overall_mean']}**（basis: "
              f"{run['aggregate']['overall_basis']}）", ""]
    # failure evidence
    lines.append("## 确定性检查失败证据")
    for cr in run["cases"]:
        det = cr["deterministic"]["checks"]
        evidences = []
        if det["no_boilerplate"]["detail"]["hits"]:
            evidences.append(f"boilerplate 残留: {det['no_boilerplate']['detail']['hits']}")
        if det["highlight_dedup"]["detail"]["duplicate_keys"]:
            evidences.append(f"重复高亮: {det['highlight_dedup']['detail']['duplicate_keys']}")
        for diff in det["translation_consistency"]["detail"]["diffs"]:
            evidences.append(
                f"译文不一致[{diff['paragraph_id']}]: 长难句译「"
                f"{diff['sentence_translation'][:60]}…」 不在段译中（段译开头「"
                f"{diff['paragraph_translation_excerpt'][:60]}…」）"
            )
        if det["gold_expression_coverage"]["detail"].get("missing"):
            cov = det["gold_expression_coverage"]["detail"]
            evidences.append(f"金标表达覆盖 {cov['coverage']}，缺失: {cov['missing']}")
        if evidences:
            lines.append(f"### {cr['case_id']}")
            lines += [f"- {e}" for e in evidences]
            lines.append("")
    if run["judge_status"] == "enabled":
        lines.append("## LLM judge 维度分")
        dim_ids = [d["id"] for d in run["rubric_judge_dimensions"]]
        lines.append("| case | " + " | ".join(dim_ids) + " |")
        lines.append("|---|" + "---|" * len(dim_ids))
        for cr in run["cases"]:
            dims = cr["judge"].get("dimensions", {}) if cr["judge"]["status"] == "ok" else {}
            cells = [str(dims.get(d, {}).get("score", "—")) for d in dim_ids]
            lines.append(f"| {cr['case_id']} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines) + "\n"


def load_judge_env_file(path: str) -> None:
    """Import KEY=VALUE pairs from an env file (e.g. services/api/.env).

    Only fills variables not already set; never prints values.
    """
    import os
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily Reader regression eval (A-6)")
    ap.add_argument("--mode", choices=["baseline", "workflow"], default="baseline")
    ap.add_argument("--case", action="append", default=[], help="case id (repeatable); default all")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--dataset-dir", default=str(DEFAULT_DATASET_DIR))
    ap.add_argument("--runs-dir", default=str(EVALS_ROOT / "runs"))
    ap.add_argument("--no-judge", action="store_true", help="skip LLM judge even if configured")
    ap.add_argument("--judge-env-file", default=None,
                    help="env file to load judge credentials from (e.g. ../services/api/.env)")
    args = ap.parse_args()

    if args.judge_env_file:
        load_judge_env_file(args.judge_env_file)

    import os
    if args.mode == "workflow" and os.environ.get(GATE_ENV) != "1":
        sys.exit(f"--mode workflow makes paid LLM calls; set {GATE_ENV}=1 to proceed")

    dataset_dir = Path(args.dataset_dir)
    rubric = yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))
    cases = load_cases(dataset_dir, args.case)
    run_id = args.run_id or (
        f"daily-reader-{args.mode}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    run_dir = Path(args.runs_dir) / run_id
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    case_results = []
    for case in cases:
        cid = case["case_id"]
        print(f"[{args.mode}] {cid} ...", flush=True)
        if args.mode == "baseline":
            origin = case.get("origin", {})
            if origin.get("kind") != "production_row":
                print("  skip: synthetic case has no stored row")
                continue
            artifact = fetch_baseline_row(origin["daily_reader_id"])
            if artifact is None:
                sys.exit(f"row {origin['daily_reader_id']} not found in daily_readers")
        else:
            case_path = dataset_dir / "cases" / f"{cid}.json"
            out_path = artifacts_dir / f"{cid}.workflow.json"
            run_workflow_harness(case_path, out_path)
            artifact = json.loads(out_path.read_text(encoding="utf-8"))

        artifact["case_id"] = cid
        (artifacts_dir / f"{cid}.artifact.json").write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = score_case(rubric, case, artifact, skip_judge=args.no_judge)
        case_results.append({
            "case_id": cid,
            "expected_difficulty": case["gold"].get("expected_difficulty"),
            **result,
        })
        print(f"  deterministic {result['deterministic']['passed']}/"
              f"{result['deterministic']['total']} · judge={result['judge']['status']} · "
              f"overall={result['overall']}")

    judge_statuses = {cr["judge"]["status"] for cr in case_results}
    judge_status = ("enabled" if "ok" in judge_statuses
                    else "skipped" if judge_statuses <= {"skipped"}
                    else "partial_error")
    if args.no_judge:
        judge_status = "disabled_by_flag"
    basis = ("det+judge" if any(cr["judge_mean"] is not None for cr in case_results)
             else "deterministic_only")
    run = {
        "schema_kind": "daily_reader_regression_run",
        "run_id": run_id,
        "mode": args.mode,
        "dataset_id": "daily-reader-regression-v1",
        "dataset_dir": str(dataset_dir),
        "rubric_id": rubric["id"],
        "rubric_judge_dimensions": rubric["judge"]["dimensions"],
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "judge_status": judge_status,
        "judge_model": next((cr["judge"].get("model") for cr in case_results
                             if cr["judge"]["status"] == "ok"), None),
        "cases": case_results,
        "aggregate": {
            "case_count": len(case_results),
            "deterministic_pass_ratio_mean": round(
                sum(cr["deterministic"]["pass_ratio"] for cr in case_results)
                / len(case_results), 4) if case_results else 0.0,
            "judge_mean_mean": round(
                sum(cr["judge_mean"] for cr in case_results if cr["judge_mean"] is not None)
                / max(1, sum(1 for cr in case_results if cr["judge_mean"] is not None)), 3)
            if any(cr["judge_mean"] is not None for cr in case_results) else None,
            "overall_mean": round(
                sum(cr["overall"] for cr in case_results) / len(case_results), 4)
            if case_results else 0.0,
            "overall_basis": basis,
        },
    }
    (run_dir / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(render_report_md(run), encoding="utf-8")
    print(f"\nrun written: {run_dir}")
    print(f"overall_mean={run['aggregate']['overall_mean']} ({basis})")


if __name__ == "__main__":
    main()
