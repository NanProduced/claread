"""Aggregate blind judgments for the translation-grouping eval.

Reads the tracked post-eval side assignment + per-article judgments,
unblinds the completed verdicts, and writes an aggregate evidence report
(JSON + Markdown). The assignment was not available to judges during eval.

    python evals/scripts/aggregate_translation_grouping_eval.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "evals" / "datasets" / "translation-grouping-v1"
JUDGMENTS_DIR = DATASET_DIR / "results" / "judgments"
SIDE_ASSIGNMENT_PATH = DATASET_DIR / "results" / "post-eval-side-assignment.json"
REPORT_JSON = DATASET_DIR / "results" / "aggregate.json"
REPORT_MD = DATASET_DIR / "results" / "report.md"

LIMITATIONS = [
    {
        "code": "same_family_sub_agents",
        "detail": (
            "The semantic arm and judges were produced by the same sub-agent "
            "family, so same-family preference may remain."
        ),
    },
    {
        "code": "non_human_non_independent_evaluators",
        "detail": "Judges were neither human evaluators nor an independent model family.",
    },
    {
        "code": "semantic_arm_not_production_planner",
        "detail": "The semantic arm was an evaluation proxy, not the production candidate planner.",
    },
]
INTERPRETATION = {
    "classification": "strong_exploratory_directional_preference_signal",
    "summary": (
        "Strong exploratory/directional preference signal, not unconditional "
        "evidence of significant benefit."
    ),
    "production_planner_authorized": False,
    "next_comparison_requires": [
        "production candidate planner",
        "independent model family or human evaluators",
        "blind side assignment",
    ],
}

DIMENSIONS = ["coherence", "boundary_naturalness", "granularity", "structural_respect"]
WEIGHTS = {
    "coherence": 0.35,
    "boundary_naturalness": 0.30,
    "granularity": 0.25,
    "structural_respect": 0.10,
}


def wilson_interval(wins: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = wins / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = (z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def weighted_mean(scores: dict) -> float:
    return sum(WEIGHTS[d] * scores[d] for d in DIMENSIONS) / sum(WEIGHTS.values())


def main() -> None:
    side_assignment = json.loads(SIDE_ASSIGNMENT_PATH.read_text(encoding="utf-8"))
    manifest = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
    categories = {entry["id"]: entry["category"] for entry in manifest}

    rows: list[dict] = []
    problems: list[str] = []
    for judgment_file in sorted(JUDGMENTS_DIR.glob("*.json")):
        article_id = judgment_file.stem
        judgment = json.loads(judgment_file.read_text(encoding="utf-8"))
        if judgment.get("id") != article_id or judgment.get("winner") not in {"X", "Y", "tie"}:
            problems.append(f"{article_id}: malformed judgment")
            continue
        assignment = side_assignment["articles"][article_id]
        x_arm = assignment["X"]
        y_arm = assignment["Y"]
        if {x_arm, y_arm} != {"deterministic", "semantic"}:
            problems.append(f"{article_id}: malformed side assignment")
            continue
        verdict = judgment["winner"]
        if verdict == "tie":
            outcome = "tie"
        elif (verdict == "X" and x_arm == "semantic") or (verdict == "Y" and y_arm == "semantic"):
            outcome = "semantic_win"
        else:
            outcome = "deterministic_win"
        x_scores = judgment["scores"]["X"]
        y_scores = judgment["scores"]["Y"]
        arm_scores = {
            x_arm: x_scores,
            y_arm: y_scores,
        }
        rows.append(
            {
                "id": article_id,
                "category": categories[article_id],
                "x_arm": x_arm,
                "verdict": verdict,
                "outcome": outcome,
                "weighted_scores": {
                    arm: round(weighted_mean(arm_scores[arm]), 3)
                    for arm in ("deterministic", "semantic")
                },
                "rationale": judgment.get("rationale", ""),
            }
        )

    def bucket(items: list[dict]) -> dict:
        total = len(items)
        wins = sum(1 for item in items if item["outcome"] == "semantic_win")
        ties = sum(1 for item in items if item["outcome"] == "tie")
        lo, hi = wilson_interval(wins, total)
        return {
            "total": total,
            "semantic_wins": wins,
            "deterministic_wins": total - wins - ties,
            "ties": ties,
            "semantic_win_rate": round(wins / total, 3) if total else None,
            "wilson95": [round(lo, 3), round(hi, 3)],
            "mean_weighted_score": {
                "deterministic": round(
                    sum(item["weighted_scores"]["deterministic"] for item in items) / total, 3
                )
                if total
                else None,
                "semantic": round(
                    sum(item["weighted_scores"]["semantic"] for item in items) / total, 3
                )
                if total
                else None,
            },
        }

    per_category = {
        category: bucket([row for row in rows if row["category"] == category])
        for category in ("short-news", "long-form", "structural")
    }
    aggregate = {
        "dataset": "translation-grouping-v1",
        "seed": side_assignment["seed"],
        "rubric": "translation-grouping-blind-v1",
        "judge": (
            "blind same-family sub-agents (one per article, no access to side "
            "assignment during judging)"
        ),
        "arms": {
            "deterministic": "production deterministic planner",
            "semantic": (
                "same-family sub-agent semantic grouping proxy (not a production candidate planner)"
            ),
        },
        "interpretation": INTERPRETATION,
        "limitations": LIMITATIONS,
        "articles": rows,
        "per_category": per_category,
        "overall": bucket(rows),
        "problems": problems,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# Translation grouping blind eval — aggregate report",
        "",
        (
            "- Dataset: translation-grouping-v1 (24 articles, 8 per category, "
            "real licensed texts, manifest.json)"
        ),
        (
            "- Arms: deterministic = production deterministic planner; "
            "semantic = same-family sub-agent grouping proxy "
            "(not a production candidate planner)"
        ),
        (
            "- Judge: 24 blind same-family sub-agent judgments (one per article); "
            f"sides randomized (seed {side_assignment['seed']}), assignment "
            "withheld during judging"
        ),
        (
            "- Rubric: translation-grouping-blind-v1 (coherence .35 / "
            "boundary_naturalness .30 / granularity .25 / "
            "structural_respect .10)"
        ),
        "",
        "## Results",
        "",
        (
            "| bucket | articles | semantic wins | deterministic wins | ties | "
            "semantic win rate (Wilson 95%) | mean weighted det vs sem |"
        ),
        "|---|---|---|---|---|---|---|",
    ]
    for name, stats in [
        ("short-news", per_category["short-news"]),
        ("long-form", per_category["long-form"]),
        ("structural", per_category["structural"]),
        ("overall", aggregate["overall"]),
    ]:
        mean_scores = stats["mean_weighted_score"]
        lines.append(
            f"| {name} | {stats['total']} | {stats['semantic_wins']} | "
            f"{stats['deterministic_wins']} | {stats['ties']} | "
            f"{stats['semantic_win_rate']} {stats['wilson95']} | "
            f"{mean_scores['deterministic']} vs {mean_scores['semantic']} |"
        )
    lines += [
        "",
        "## Interpretation and limitations",
        "",
        (
            "- Interpretation: strong exploratory/directional preference signal; "
            "this is not unconditional evidence of significant benefit."
        ),
        (
            "- Same-family limitation: the semantic arm and judges came from the "
            "same sub-agent family, so same-family preference may remain."
        ),
        (
            "- Evaluator limitation: judges were neither human evaluators nor an "
            "independent model family."
        ),
        (
            "- Planner limitation: the semantic arm was an evaluation proxy, "
            "not the production candidate planner."
        ),
        "- Decision: this evidence does not authorize a production two-stage LLM planner.",
        (
            "- Next comparison: use a production candidate planner, an independent "
            "model family or human evaluators, and keep blind side assignment."
        ),
    ]
    lines += ["", "## Per-article outcomes", ""]
    for row in rows:
        scores = row["weighted_scores"]
        lines.append(
            f"- {row['id']} ({row['category']}): {row['outcome']} "
            f"(det {scores['deterministic']} vs sem {scores['semantic']}) — "
            f"{row['rationale']}"
        )
    if problems:
        lines += ["", "## Problems", ""] + [f"- {p}" for p in problems]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "overall": aggregate["overall"],
                "per_category": aggregate["per_category"],
                "problems": problems,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
