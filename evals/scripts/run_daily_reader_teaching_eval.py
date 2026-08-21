"""Daily Reader teaching-contract v2 artifact runner (P-2).

Minimal offline runner: reads frozen schema-2 cases, pre-built artifact
JSONs and per-case review placeholders from directories, scores them
against ``rubrics/daily-reader-teaching-v2.yaml`` (12 hard gates + the
eight-dimension judge contract) and writes a reproducible ``run.json``
plus ``report.md``. No subprocess/docker, no HTTP, no DB: structurally
zero network and zero database. Judge execution lands in P-3; here a
missing judge yields ``SEMANTIC_NOT_RUN`` and a missing human review
yields ``HUMAN_REVIEW_PENDING`` — never a quality PASS.

Usage (run from ``evals/``):

    uv run python scripts/run_daily_reader_teaching_eval.py \
        --dataset-dir datasets/daily-reader-teaching-v2 \
        --artifacts-dir tests/fixtures/daily_reader_teaching_v2/artifacts \
        --runs-dir <tmp-dir> --no-judge
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

EVALS_ROOT = Path(__file__).resolve().parents[1]
if str(EVALS_ROOT) not in sys.path:
    sys.path.insert(0, str(EVALS_ROOT))

import yaml  # noqa: E402

from claread_eval.daily_reader.teaching_v2 import gates as g2  # noqa: E402
from claread_eval.daily_reader.teaching_v2 import judge as j2  # noqa: E402
from claread_eval.daily_reader.teaching_v2 import report as rp  # noqa: E402
from claread_eval.daily_reader.teaching_v2 import review as rv  # noqa: E402

DEFAULT_DATASET_DIR = EVALS_ROOT / "datasets" / "daily-reader-teaching-v2"
RUBRIC_PATH = EVALS_ROOT / "rubrics" / "daily-reader-teaching-v2.yaml"
OVERALL_PASS_THRESHOLD = 0.90
JUDGE_PASS_SCORE = 4


def load_cases(dataset_dir: Path, case_ids: list[str]) -> list[dict[str, Any]]:
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


def load_artifact(artifacts_dir: Path, case_id: str) -> dict[str, Any]:
    path = artifacts_dir / f"{case_id}.artifact.json"
    if not path.exists():
        sys.exit(f"missing artifact for case {case_id}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_review(dataset_dir: Path, case_id: str) -> dict[str, Any] | None:
    path = dataset_dir / "reviews" / f"{case_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _is_reject(case: dict[str, Any], artifact: dict[str, Any]) -> bool:
    return (case.get("gold", {}).get("expected_outcome") == "reject"
            or (artifact.get("run_meta") or {}).get("outcome") == "reject")


def score_case(rubric: dict[str, Any], case: dict[str, Any], artifact: dict[str, Any],
               review_doc: dict[str, Any] | None = None, skip_judge: bool = False,
               judge_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score one case offline. Verdict ladder: reject handled by gold ->
    PASS; any hard gate failing -> FAIL; judge absent -> SEMANTIC_NOT_RUN;
    human gate incomplete -> HUMAN_REVIEW_PENDING; otherwise PASS requires
    every judge dimension >= 4 and overall >= 0.90."""
    gates = g2.run_hard_gates(case, artifact)
    det_ratio = (gates["passed_count"] / gates["scored_count"]
                 if gates["scored_count"] else 1.0)
    reject = _is_reject(case, artifact)

    if reject:
        judge: dict[str, Any] = {"status": "not_applicable_rejected",
                                 "reason": "rejected run carries no teaching package"}
    elif skip_judge or judge_result is None:
        judge = {"status": j2.SEMANTIC_NOT_RUN,
                 "reason": "--no-judge" if skip_judge else "judge result absent (P-3)"}
    else:
        judge = judge_result

    judge_mean = j2.judge_mean_v2(judge)
    if judge_mean is not None:
        overall: float | None = round(0.5 * det_ratio + 0.5 * (judge_mean / 5.0), 4)
    elif reject:
        overall = round(det_ratio, 4)
    else:
        overall = None  # never hand out a PASS-able number without the judge

    review = rv.review_status(case, artifact, review_doc)

    if reject:
        verdict = "PASS" if gates["all_passed"] else "FAIL"
    elif not gates["all_passed"]:
        verdict = "FAIL"
    elif judge.get("status") != "ok":
        verdict = j2.SEMANTIC_NOT_RUN
    elif not review["accepted"]:
        verdict = rv.HUMAN_REVIEW_PENDING
    else:
        dims_ok = all(d["score"] >= JUDGE_PASS_SCORE
                      for d in judge.get("dimensions", {}).values())
        verdict = "PASS" if dims_ok and overall is not None \
            and overall >= OVERALL_PASS_THRESHOLD else "FAIL"

    return {
        "verdict": verdict,
        "gates": gates,
        "deterministic_pass_ratio": round(det_ratio, 4),
        "judge": judge,
        "judge_mean": round(judge_mean, 3) if judge_mean is not None else None,
        "overall": overall,
        "review": review,
        "cost": rp.cost_block((artifact.get("run_meta") or {}).get("usage")),
    }


def decorate(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    gold = case.get("gold", {})
    return {
        "case_id": case["case_id"],
        "article_type": gold.get("article_type"),
        "difficulty": gold.get("expected_difficulty"),
        "source": case.get("input", {}).get("source"),
        **result,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily Reader teaching-contract v2 eval (P-2)")
    ap.add_argument("--dataset-dir", default=str(DEFAULT_DATASET_DIR))
    ap.add_argument("--artifacts-dir", required=True,
                    help="directory containing <case_id>.artifact.json")
    ap.add_argument("--runs-dir", default=str(EVALS_ROOT / "runs"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--case", action="append", default=[],
                    help="case id (repeatable); default all")
    ap.add_argument("--no-judge", action="store_true",
                    help="skip the (P-3) judge entirely; judge -> SEMANTIC_NOT_RUN")
    args = ap.parse_args()

    dataset_dir = Path(args.dataset_dir)
    rubric = yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))
    cases = load_cases(dataset_dir, args.case)
    artifacts_dir = Path(args.artifacts_dir)
    run_id = args.run_id or (
        f"daily-reader-teaching-v2-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
    run_dir = Path(args.runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    case_results = []
    for case in cases:
        cid = case["case_id"]
        print(f"[artifact] {cid} ...", flush=True)
        artifact = load_artifact(artifacts_dir, cid)
        review_doc = load_review(dataset_dir, cid)
        result = score_case(rubric, case, artifact, review_doc=review_doc,
                            skip_judge=args.no_judge)
        case_results.append(decorate(case, result))
        print(f"  gates {result['gates']['passed_count']}/"
              f"{result['gates']['scored_count']} · judge={result['judge']['status']} · "
              f"review={result['review']['status']} · verdict={result['verdict']}")

    judge_status = ("disabled_by_flag" if args.no_judge else "not_run_in_p2")
    run = rp.build_run(run_id=run_id, dataset_id="daily-reader-teaching-v2",
                       dataset_dir=str(dataset_dir), rubric=rubric,
                       case_results=case_results, judge_status=judge_status,
                       created_at=dt.datetime.now().isoformat(timespec="seconds"))
    (run_dir / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(rp.render_report_md(run), encoding="utf-8")
    agg = run["aggregate"]
    print(f"\nrun written: {run_dir}")
    print(f"PASS {agg['pass_count']}/{agg['case_count']} · verdicts={agg['verdicts']} · "
          f"overall_mean={agg['overall_mean']}（仅观察）")


if __name__ == "__main__":
    main()
