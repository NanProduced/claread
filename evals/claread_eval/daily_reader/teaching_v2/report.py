"""Run assembly and markdown report for the teaching v2 artifact runner.

``cost_block`` implements the P-2 cost placeholder contract: a measured
non-negative usage dict passes through field-by-field (never overwritten
to null); a completely missing usage yields the exact
``NOT_RUN_OWNER_REQUIRED`` block with ten null fields. Real cost
baselines belong to P-3.
"""

from __future__ import annotations

import json
from typing import Any

from claread_eval.daily_reader.teaching_v2 import gates as g2
from claread_eval.daily_reader.teaching_v2 import review as rv

_COST_FIELDS = (
    "provider_requests", "logical_llm_calls", "retry_count",
    "output_retry_count", "refinement_count", "per_agent_tokens",
    "per_agent_latency_ms", "end_to_end_latency_ms",
    "accepted_teaching_points", "keep_points_per_1000_output_tokens",
)
_DICT_FIELDS = {"per_agent_tokens", "per_agent_latency_ms"}


def _valid_cost_value(field: str, value: Any) -> bool:
    """Measured cost values must be non-negative numbers (dicts for the two
    per-agent breakdown fields); bools are not numbers here."""
    if value is None:
        return True
    if field in _DICT_FIELDS:
        return isinstance(value, dict)
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0


def cost_block(usage: dict[str, Any] | None) -> dict[str, Any]:
    if not usage:
        return {"status": "NOT_RUN_OWNER_REQUIRED",
                **{f: None for f in _COST_FIELDS}}
    block: dict[str, Any] = {"status": "measured"}
    warnings: list[str] = []
    for f in _COST_FIELDS:
        value = usage.get(f)
        if _valid_cost_value(f, value):
            block[f] = value  # measured non-negative values pass through
        else:
            block[f] = None  # invalid -> nulled, never trusted
            warnings.append(f"{f}: invalid value {value!r} nulled")
    if warnings:
        block["warnings"] = warnings
    return block


def build_strata(case_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """type/difficulty/source strata, computed once. A stratum is accepted
    only if every case inside it is PASS — never averaged."""
    strata: dict[str, dict[str, Any]] = {}
    for axis in ("article_type", "difficulty", "source"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for cr in case_results:
            groups.setdefault(str(cr.get(axis)), []).append(cr)
        strata[axis] = {
            key: {
                "count": len(items),
                "pass_count": sum(1 for cr in items if cr["verdict"] == "PASS"),
                "verdicts": [cr["verdict"] for cr in items],
                "accepted": all(cr["verdict"] == "PASS" for cr in items),
            }
            for key, items in sorted(groups.items())
        }
    return strata


def _completed_eight_dim_publish(cr: dict[str, Any]) -> bool:
    """overall_mean only counts cleaned_publish cases with a complete 8-dim judge."""
    if cr.get("expected_outcome") != "cleaned_publish":
        return False
    judge = cr.get("judge") or {}
    dims = judge.get("dimensions") if judge.get("status") == "ok" else None
    return (isinstance(dims, dict) and len(dims) == 8
            and isinstance(cr.get("overall"), int | float))


def build_run(*, run_id: str, dataset_id: str, dataset_dir: str, rubric: dict[str, Any],
              case_results: list[dict[str, Any]], judge_status: str,
              created_at: str) -> dict[str, Any]:
    pass_cases = [cr for cr in case_results if cr["verdict"] == "PASS"]
    expected_rejects = [cr for cr in case_results if cr["verdict"] == "EXPECTED_REJECT"]
    overalls = [cr["overall"] for cr in case_results if _completed_eight_dim_publish(cr)]
    return {
        "schema_kind": "daily_reader_teaching_v2_run",
        "run_id": run_id,
        "mode": "artifact",
        "dataset_id": dataset_id,
        "dataset_dir": dataset_dir,
        "rubric_id": rubric["id"],
        "rubric_judge_dimensions": rubric["judge"]["dimensions"],
        "created_at": created_at,
        "judge_status": judge_status,
        "cases": case_results,
        "strata": build_strata(case_results),
        "strata_all_accepted": rv.strata_all_accepted(
            {k: s for axis in build_strata(case_results).values()
             for k, s in axis.items()}),
        "aggregate": {
            "case_count": len(case_results),
            "pass_count": len(pass_cases),
            "expected_reject_count": len(expected_rejects),
            "verdicts": {cr["verdict"]: sum(1 for c in case_results
                                            if c["verdict"] == cr["verdict"])
                         for cr in case_results},
            "overall_mean": round(sum(overalls) / len(overalls), 4)
            if overalls else None,
            "overall_mean_note": "观察值：只统计完成八维 Judge 的 cleaned_publish；"
                                 "全部 SEMANTIC_NOT_RUN 时为 null；"
                                 "EXPECTED_REJECT 不计入质量 PASS",
        },
    }


def _mark(passed: bool | None) -> str:
    return "✅" if passed is True else ("n/a" if passed is None else "❌")


def render_report_md(run: dict[str, Any]) -> str:
    gate_ids = list(g2.HARD_GATES)
    lines = [
        f"# Daily Reader 教学合同 v2 评测报告 — {run['run_id']}",
        "",
        f"- mode: `{run['mode']}` · dataset: `{run['dataset_id']}` · "
        f"rubric: `{run['rubric_id']}`",
        f"- 时间: {run['created_at']} · judge: {run['judge_status']}",
        "",
        "## 矩阵覆盖",
        "",
    ]
    for axis, groups in run["strata"].items():
        cells = ", ".join(f"{k}×{v['count']}（PASS {v['pass_count']}/{v['count']}）"
                          for k, v in groups.items())
        lines.append(f"- {axis}: {cells}")
    lines += [
        "",
        "## 逐篇硬门禁",
        "",
        "| case | type | diff | " + " | ".join(gate_ids) + " | verdict |",
        "|---|---|---|" + "---|" * len(gate_ids) + "---|",
    ]
    for cr in run["cases"]:
        cells = " | ".join(_mark(g["passed"]) for g in cr["gates"]["gates"].values())
        lines.append(f"| {cr['case_id']} | {cr['article_type']} | {cr['difficulty']} "
                     f"| {cells} | **{cr['verdict']}** |")
    dim_ids = [d["id"] for d in run["rubric_judge_dimensions"]]
    lines += ["", "## 八维 Judge 状态", "",
              "| case | judge | " + " | ".join(dim_ids) + " |",
              "|---|---|" + "---|" * len(dim_ids)]
    for cr in run["cases"]:
        dims = cr["judge"].get("dimensions", {}) if cr["judge"].get("status") == "ok" else {}
        cells = " | ".join(str(dims.get(d, {}).get("score", "—")) for d in dim_ids)
        lines.append(f"| {cr['case_id']} | {cr['judge'].get('status')} | {cells} |")
    lines += ["", "## 人工审阅状态", "",
              "| case | review | accepted | overall |",
              "|---|---|---|---|"]
    for cr in run["cases"]:
        lines.append(f"| {cr['case_id']} | {cr['review']['status']} "
                     f"| {cr['review']['accepted']} | {cr['overall']} |")
    lines += ["", "## 成本状态", ""]
    for cr in run["cases"]:
        c = cr.get("cost") or {}
        if c.get("status") == "measured":
            lines.append(f"- {cr['case_id']}: measured · provider_requests="
                         f"{c.get('provider_requests')} · logical_llm_calls="
                         f"{c.get('logical_llm_calls')} · end_to_end_latency_ms="
                         f"{c.get('end_to_end_latency_ms')}")
        else:
            lines.append(f"- {cr['case_id']}: {c.get('status')}")
    lines += ["", "## 分层（独立判定，不平均）", ""]
    for axis, groups in run["strata"].items():
        for key, s in groups.items():
            lines.append(f"- {axis}={key}: PASS {s['pass_count']}/{s['count']}，"
                         f"accepted={s['accepted']}，verdicts={s['verdicts']}")
    agg = run["aggregate"]
    lines += [
        "",
        "## 质量 PASS 与预期拒绝",
        "",
        f"- 质量 PASS: {agg['pass_count']}",
        f"- EXPECTED_REJECT: {agg.get('expected_reject_count', 0)}",
        "",
        f"overall mean（仅观察）: **{agg['overall_mean']}** — "
        f"{agg['overall_mean_note']}", "", "## 失败证据", ""]
    any_evidence = False
    for cr in run["cases"]:
        evidences = []
        if cr.get("schema_errors"):
            evidences.append(f"schema: {json.dumps(cr['schema_errors'], ensure_ascii=False)}")
        evidences += [f"{gid}: {json.dumps(g['detail'], ensure_ascii=False)}"
                      for gid, g in cr["gates"]["gates"].items() if g["passed"] is False]
        if cr["judge"].get("status") not in ("ok", "SEMANTIC_NOT_RUN",
                                             "not_applicable_rejected"):
            evidences.append(f"judge: {cr['judge']}")
        if not cr["review"]["accepted"] and cr["review"]["status"] != rv.HUMAN_REVIEW_PENDING:
            review_issue = cr["review"].get("problems") or cr["review"].get("missing_items")
            evidences.append(f"review: {review_issue}")
        if evidences:
            any_evidence = True
            lines.append(f"### {cr['case_id']}")
            lines += [f"- {e}" for e in evidences]
            lines.append("")
    if not any_evidence:
        lines += ["（无）", ""]
    return "\n".join(lines) + "\n"
