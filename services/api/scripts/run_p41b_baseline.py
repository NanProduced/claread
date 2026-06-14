"""P4.1B baseline runner.

Safe by default:
- dry-run unless ``--run-real`` is passed;
- never enables ``CLAREAD_ALLOW_REAL_LLM_TESTS`` by itself;
- runs one sample at a time unless ``--all`` is explicitly passed.

Examples:
    .venv/Scripts/python.exe scripts/run_p41b_baseline.py --sample sample-1

    $env:CLAREAD_ALLOW_REAL_LLM_TESTS="1"
    .venv/Scripts/python.exe scripts/run_p41b_baseline.py --sample sample-1 --run-real
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Add project root to path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SAMPLES_DIR = PROJECT_ROOT.parent.parent / "tmp" / "baseline-sample"
DEFAULT_MODEL_PROFILE = "workflow-qwen36-plus"


def load_samples() -> list[dict]:
    """Load sample texts from tmp/baseline-sample/."""
    samples = []
    for p in sorted(SAMPLES_DIR.glob("sample-*.txt")):
        text = p.read_text(encoding="utf-8").strip()
        samples.append({"id": p.stem, "text": text, "chars": len(text)})
    return samples


def select_samples(
    samples: list[dict],
    *,
    sample_id: str | None,
    all_samples: bool,
) -> list[dict]:
    """Select samples for this run."""
    if all_samples:
        return samples
    if not sample_id:
        raise SystemExit("Pass --sample sample-N, or pass --all explicitly.")
    selected = [s for s in samples if s["id"] == sample_id]
    if not selected:
        known = ", ".join(s["id"] for s in samples) or "<none>"
        raise SystemExit(f"Unknown sample {sample_id!r}. Known samples: {known}")
    return selected


def print_plan(
    samples: list[dict],
    *,
    model_profile: str,
    run_real: bool,
    target: str,
    node: str,
    reading_goal: str,
    reading_variant: str,
    repair_mode: str,
) -> None:
    """Print the exact run plan before any possible model call."""
    mode = "REAL LLM" if run_real else "DRY RUN"
    print(f"Mode: {mode}")
    print(f"Target: {target}" + (f" ({node})" if target == "node-probe" else ""))
    print(f"Model profile: {model_profile}")
    print(f"Reading goal/variant: {reading_goal}/{reading_variant}")
    print(f"Repair mode: {repair_mode}")
    print(f"Samples: {len(samples)}")
    for sample in samples:
        print(f"  - {sample['id']}: {sample['chars']} chars")
    print("=" * 80)


def build_run_output_dir(
    *,
    samples: list[dict],
    run_real: bool,
    target: str,
    node: str,
    repair_mode: str,
) -> Path:
    """Create a unique output directory for this script invocation."""
    run_mode = "real" if run_real else "dryrun"
    sample_scope = "all" if len(samples) != 1 else str(samples[0]["id"])
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_id = f"{timestamp}_{run_mode}_{target}_{node}_{sample_scope}_{repair_mode}"
    output_dir = SAMPLES_DIR / "runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir


def ensure_real_run_allowed(*, run_real: bool, all_samples: bool) -> None:
    """Guard expensive calls behind both CLI and environment opt-ins."""
    if not run_real:
        return
    if os.getenv("CLAREAD_ALLOW_REAL_LLM_TESTS") != "1":
        raise SystemExit(
            "Refusing real LLM run: set CLAREAD_ALLOW_REAL_LLM_TESTS=1 in this "
            "shell and pass --run-real."
        )
    if all_samples and os.getenv("CLAREAD_ALLOW_REAL_LLM_ALL_SAMPLES") != "1":
        raise SystemExit(
            "Refusing multi-sample real LLM run: set "
            "CLAREAD_ALLOW_REAL_LLM_ALL_SAMPLES=1 as an additional opt-in."
        )


def extract_anchor_kind_distribution(render_scene) -> dict:
    """Count anchor.kind distribution from render_scene.inline_marks."""
    if not render_scene or not hasattr(render_scene, "inline_marks"):
        return {}
    counts: dict[str, int] = {}
    for mark in render_scene.inline_marks:
        anchor = getattr(mark, "anchor", None)
        if anchor:
            kind = getattr(anchor, "kind", "unknown")
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def extract_range_warnings(render_scene) -> list:
    """Extract range/projection related warnings from render_scene."""
    if not render_scene or not hasattr(render_scene, "warnings"):
        return []
    return [
        w for w in render_scene.warnings
        if "range" in getattr(w, "code", "").lower()
        or "projection" in getattr(w, "code", "").lower()
        or "canonical_range" in getattr(w, "code", "").lower()
    ]


async def run_single_sample(
    sample: dict,
    *,
    model_profile: str,
    reading_goal: str,
    reading_variant: str,
    rag_mode: str,
    timeout_seconds: float | None,
    repair_mode: str,
) -> dict:
    """Run eval for a single sample and collect metrics."""
    from app.eval_adapter.article_analysis import run_article_analysis_eval
    from app.eval_adapter.schemas import ArticleAnalysisEvalRequest
    from app.eval_adapter.shared import build_llm_config_snapshot
    from app.llm.types import ModelSelection

    model_selection = ModelSelection(default_profile=model_profile)
    request = ArticleAnalysisEvalRequest(
        text=sample["text"],
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        model_selection=model_selection,
        rag_mode=rag_mode,
        trace_scope="off",
        timeout_seconds=timeout_seconds,
        repair_mode=repair_mode,
    )

    t0 = time.perf_counter()
    result = await run_article_analysis_eval(request)
    elapsed = time.perf_counter() - t0

    # Collect metrics
    metrics: dict = {
        "sample_id": sample["id"],
        "chars": sample["chars"],
        "wall_clock_s": round(elapsed, 2),
        "status": result.status,
        "repair_mode": repair_mode,
    }
    if result.model_identity:
        metrics["model_identity"] = result.model_identity.model_dump(mode="json")
    if result.error:
        metrics["error"] = result.error.model_dump(mode="json")

    # node_timings (flat dict: key -> float seconds)
    if result.node_timings:
        metrics["node_timings"] = result.node_timings
        wt = result.node_timings.get("workflow_total")
        metrics["workflow_total_s"] = wt if isinstance(wt, int | float) else None

    # usage_summary (via runtime_summary)
    if result.runtime_summary:
        rs = result.runtime_summary
        metrics["total_tokens"] = rs.get("aggregate", {}).get("total_tokens")
        metrics["input_tokens"] = rs.get("aggregate", {}).get("input_tokens")
        metrics["output_tokens"] = rs.get("aggregate", {}).get("output_tokens")
        metrics["per_agent_tokens"] = rs.get("per_agent")

    # repair_stats
    if result.repair_stats:
        metrics["repair_triggered"] = result.repair_stats.get("repair_triggered")
        metrics["repair_succeeded"] = result.repair_stats.get("repair_succeeded")
        metrics["repair_elapsed_s"] = result.repair_stats.get("repair_elapsed_s")

    # annotation_stats
    if result.annotation_stats:
        metrics["annotation_stats"] = result.annotation_stats
        cs = result.annotation_stats.get("canonical_stats", {})
        if cs:
            metrics["canonical_span_count"] = cs.get("canonical_span_count")
            metrics["canonical_normalized_counts"] = cs.get("canonical_normalized_counts")

    # drop_log
    if result.drop_log:
        metrics["drop_count"] = len(result.drop_log)
        drop_reasons: dict[str, int] = {}
        for d in result.drop_log:
            reason = d.get("drop_reason", "unknown") if isinstance(d, dict) else "unknown"
            drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
        metrics["drop_reasons"] = drop_reasons

    # canonical_drop_log
    if result.canonical_drop_log:
        metrics["canonical_drop_count"] = len(result.canonical_drop_log)
        cdrop_reasons: dict[str, int] = {}
        for d in result.canonical_drop_log:
            reason = d.get("drop_reason", "unknown") if isinstance(d, dict) else "unknown"
            cdrop_reasons[reason] = cdrop_reasons.get(reason, 0) + 1
        metrics["canonical_drop_reasons"] = cdrop_reasons
    else:
        metrics["canonical_drop_count"] = 0

    # render_scene
    if result.render_scene:
        metrics["anchor_kind_distribution"] = extract_anchor_kind_distribution(
            result.render_scene
        )
        range_warns = extract_range_warnings(result.render_scene)
        metrics["range_warning_count"] = len(range_warns)
        metrics["range_warning_codes"] = [
            getattr(w, "code", str(w))
            for w in range_warns
        ]
        metrics["total_warnings"] = (
            len(result.render_scene.warnings)
            if hasattr(result.render_scene, "warnings")
            else 0
        )

    # Complete observation fields
    metrics["warnings"] = result.warnings
    metrics["warning_codes"] = [
        w.get("code", "unknown") if isinstance(w, dict) else "unknown"
        for w in result.warnings
    ]
    if result.runtime_summary:
        metrics["runtime_summary"] = result.runtime_summary
    metrics["drop_log"] = result.drop_log
    metrics["canonical_drop_log"] = result.canonical_drop_log
    _snapshot = build_llm_config_snapshot(model_selection, settings=None)
    metrics["llm_config_snapshot"] = (
        _snapshot.model_dump(mode="json") if _snapshot else None
    )

    return metrics


async def run_single_node_probe(
    sample: dict,
    *,
    node_name: str,
    model_profile: str,
    reading_goal: str,
    reading_variant: str,
    rag_mode: str,
    timeout_seconds: float | None,
    run_real: bool,
    repair_mode: str,
) -> dict:
    """Run or dry-run one isolated analysis node for a single sample."""
    from app.eval_adapter.node_probe import run_article_analysis_node_probe
    from app.eval_adapter.schemas import ArticleAnalysisNodeProbeRequest
    from app.eval_adapter.shared import build_llm_config_snapshot
    from app.llm.types import ModelSelection

    model_selection = ModelSelection(default_profile=model_profile)
    request = ArticleAnalysisNodeProbeRequest(
        node_name=node_name,
        text=sample["text"],
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        model_selection=model_selection,
        rag_mode=rag_mode,
        trace_scope="off",
        timeout_seconds=timeout_seconds,
        dry_run=not run_real,
    )

    t0 = time.perf_counter()
    result = await run_article_analysis_node_probe(request)
    elapsed = time.perf_counter() - t0

    metrics: dict = {
        "sample_id": sample["id"],
        "target": "node-probe",
        "node_name": node_name,
        "chars": sample["chars"],
        "wall_clock_s": round(elapsed, 2),
        "status": result.status,
        "dry_run": not run_real,
        "repair_mode": repair_mode,
        "sentence_count": len(result.prepared_sentences),
        "prompt_chars": len(result.prompt_preview or ""),
        "instruction_chars": len(result.agent_instructions or ""),
    }
    if result.model_identity:
        metrics["model_identity"] = result.model_identity.model_dump(mode="json")
    if result.example_summary:
        metrics["example_summary"] = result.example_summary
    if result.runtime_summary:
        metrics["runtime_summary"] = result.runtime_summary
        metrics["total_tokens"] = result.runtime_summary.get("aggregate", {}).get(
            "total_tokens"
        )
    if result.node_output:
        metrics["node_output"] = result.node_output
        metrics["node_output_counts"] = {
            key: len(value)
            for key, value in result.node_output.items()
            if isinstance(value, list)
        }
    if result.error:
        metrics["error"] = result.error.model_dump(mode="json")

    # Complete observation fields
    metrics["warnings"] = result.warnings
    metrics["warning_codes"] = [
        w.get("code", "unknown") if isinstance(w, dict) else "unknown"
        for w in result.warnings
    ]
    if result.runtime_summary:
        metrics["runtime_summary"] = result.runtime_summary
    _snapshot = build_llm_config_snapshot(model_selection, settings=None)
    metrics["llm_config_snapshot"] = (
        _snapshot.model_dump(mode="json") if _snapshot else None
    )

    return metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run P4.1B baseline samples safely.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--sample", help="Run one sample id, e.g. sample-1.")
    scope.add_argument(
        "--all",
        action="store_true",
        help="Run all samples. Real LLM also requires CLAREAD_ALLOW_REAL_LLM_ALL_SAMPLES=1.",
    )
    parser.add_argument(
        "--target",
        default="workflow",
        choices=("workflow", "node-probe"),
        help="Run the full workflow or one isolated node. Default: workflow.",
    )
    parser.add_argument(
        "--node",
        default="vocabulary",
        choices=("vocabulary", "grammar", "translation"),
        help="Node name for --target node-probe. Default: vocabulary.",
    )
    parser.add_argument(
        "--model-profile",
        default=DEFAULT_MODEL_PROFILE,
        help=f"Model profile to use. Default: {DEFAULT_MODEL_PROFILE}",
    )
    parser.add_argument(
        "--reading-goal",
        default="daily_reading",
        choices=("exam", "daily_reading", "academic"),
        help="Reading goal. Default: daily_reading.",
    )
    parser.add_argument(
        "--reading-variant",
        default="intermediate_reading",
        choices=(
            "gaokao",
            "cet",
            "kaoyan",
            "tem",
            "ielts_toefl",
            "beginner_reading",
            "intermediate_reading",
            "intensive_reading",
            "academic_general",
        ),
        help="Reading variant. Default: intermediate_reading.",
    )
    parser.add_argument(
        "--rag-mode",
        default="off",
        choices=("off", "baseline", "rag", "rag_fallback", "settings"),
        help="Eval adapter RAG mode. Default: off.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Per-sample timeout. Default: 180.",
    )
    parser.add_argument(
        "--run-real",
        action="store_true",
        help="Actually call the LLM. Also requires CLAREAD_ALLOW_REAL_LLM_TESTS=1.",
    )
    parser.add_argument(
        "--repair-mode",
        default="patch",
        choices=("full_result", "patch"),
        help="Repair mode for workflow target. Default: patch.",
    )
    return parser


async def main():
    args = build_parser().parse_args()
    samples = load_samples()
    samples = select_samples(samples, sample_id=args.sample, all_samples=args.all)
    print_plan(
        samples,
        model_profile=args.model_profile,
        run_real=args.run_real,
        target=args.target,
        node=args.node,
        reading_goal=args.reading_goal,
        reading_variant=args.reading_variant,
        repair_mode=args.repair_mode,
    )
    ensure_real_run_allowed(run_real=args.run_real, all_samples=args.all)

    if not args.run_real and args.target == "workflow":
        print("Dry-run only. No LLM call was made.")
        return

    output_dir = build_run_output_dir(
        samples=samples,
        run_real=args.run_real,
        target=args.target,
        node=args.node,
        repair_mode=args.repair_mode,
    )

    all_metrics = []
    for i, sample in enumerate(samples):
        print(
            f"\n[{i + 1}/{len(samples)}] Running "
            f"{sample['id']} ({sample['chars']} chars)..."
        )
        try:
            if args.target == "node-probe":
                metrics = await run_single_node_probe(
                    sample,
                    node_name=args.node,
                    model_profile=args.model_profile,
                    reading_goal=args.reading_goal,
                    reading_variant=args.reading_variant,
                    rag_mode=args.rag_mode,
                    timeout_seconds=args.timeout_seconds,
                    run_real=args.run_real,
                    repair_mode=args.repair_mode,
                )
            else:
                metrics = await run_single_sample(
                    sample,
                    model_profile=args.model_profile,
                    reading_goal=args.reading_goal,
                    reading_variant=args.reading_variant,
                    rag_mode=args.rag_mode,
                    timeout_seconds=args.timeout_seconds,
                    repair_mode=args.repair_mode,
                )
            all_metrics.append(metrics)
            if metrics.get("status") != "succeeded":
                err = metrics.get("error", {})
                code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
                message = (
                    err.get("message", "")
                    if isinstance(err, dict)
                    else str(err)
                )
                print(f"  status: {metrics.get('status')}")
                print(f"  error: {code}: {message}")
                continue
            if args.target == "node-probe":
                print(f"  status: {metrics['status']}")
                print(f"  dry_run: {metrics['dry_run']}")
                print(f"  sentences: {metrics['sentence_count']}")
                print(f"  prompt_chars: {metrics['prompt_chars']}")
                print(f"  total_tokens: {metrics.get('total_tokens', 'N/A')}")
                print(f"  output_counts: {metrics.get('node_output_counts', {})}")
                continue
            print(f"  wall_clock: {metrics['wall_clock_s']}s")
            print(f"  workflow_total: {metrics.get('workflow_total_s', 'N/A')}s")
            print(f"  total_tokens: {metrics.get('total_tokens', 'N/A')}")
            print(f"  repair_triggered: {metrics.get('repair_triggered', 'N/A')}")
            print(f"  drop_count: {metrics.get('drop_count', 0)}")
            print(f"  canonical_drop_count: {metrics.get('canonical_drop_count', 0)}")
            print(f"  anchor_kinds: {metrics.get('anchor_kind_distribution', {})}")
            print(f"  range_warnings: {metrics.get('range_warning_count', 0)}")
        except Exception as e:
            print(f"  ERROR: {e}")
            all_metrics.append(
                {
                    "sample_id": sample["id"],
                    "status": "exception",
                    "error": {"code": type(e).__name__, "message": str(e)},
                }
            )

    # ── Summary ──
    print("\n" + "=" * 80)
    print("BASELINE SUMMARY")
    print("=" * 80)

    # Save full metrics to JSON.
    output_path = output_dir / "baseline_results.json"
    output_path.write_text(
        json.dumps(all_metrics, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_mode": "real" if args.run_real else "dryrun",
                "target": args.target,
                "node": args.node,
                "model_profile": args.model_profile,
                "reading_goal": args.reading_goal,
                "reading_variant": args.reading_variant,
                "rag_mode": args.rag_mode,
                "timeout_seconds": args.timeout_seconds,
                "repair_mode": args.repair_mode,
                "samples": [
                    {"id": sample["id"], "chars": sample["chars"]}
                    for sample in samples
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nFull results saved to: {output_path}")
    print(f"Run manifest saved to: {manifest_path}")

    if args.target == "node-probe":
        print("\n-- Node Probe --")
        print(
            f"{'Sample':<12} {'Status':<10} {'DryRun':<8} "
            f"{'Sentences':<10} {'PromptChars':<12} {'Tokens':<10}"
        )
        for m in all_metrics:
            tokens_value = m.get("total_tokens")
            tokens_text = "N/A" if tokens_value is None else str(tokens_value)
            print(
                f"{m['sample_id']:<12} {m.get('status', '?'):<10} "
                f"{str(m.get('dry_run', '?')):<8} "
                f"{m.get('sentence_count', '?'):<10} "
                f"{m.get('prompt_chars', '?'):<12} {tokens_text:<10}"
            )
            if m.get("status") != "succeeded":
                err = m.get("error", {})
                code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
                message = (
                    err.get("message", "")
                    if isinstance(err, dict)
                    else str(err)
                )
                print(f"  error: {code}: {message}")
            elif m.get("node_output_counts"):
                print(f"  output_counts: {m['node_output_counts']}")
        return

    # Latency table
    print("\n-- Latency --")
    print(
        f"{'Sample':<12} {'Wall(s)':<10} {'WF Total(s)':<12} "
        f"{'Tokens':<10} {'Repair':<8}"
    )
    for m in all_metrics:
        if m.get("status") != "succeeded":
            err = m.get("error", {})
            code = err.get("code", "ERROR") if isinstance(err, dict) else "ERROR"
            print(f"{m['sample_id']:<12} {code}")
            continue
        print(
            f"{m['sample_id']:<12} {m.get('wall_clock_s', '?'):<10} "
            f"{m.get('workflow_total_s', '?'):<12} "
            f"{m.get('total_tokens', '?'):<10} "
            f"{m.get('repair_triggered', '?'):<8}"
        )

    # Aggregate stats
    valid = [m for m in all_metrics if m.get("status") == "succeeded"]
    if valid:
        wf_totals = [m["workflow_total_s"] for m in valid if m.get("workflow_total_s") is not None]
        tokens = [m["total_tokens"] for m in valid if m.get("total_tokens") is not None]
        repair_count = sum(1 for m in valid if m.get("repair_triggered") is True)

        print(f"\n── Aggregate ({len(valid)} samples) ──")
        if wf_totals:
            wf_totals.sort()
            p50 = wf_totals[len(wf_totals) // 2]
            p95 = (
                wf_totals[int(len(wf_totals) * 0.95)]
                if len(wf_totals) >= 2
                else wf_totals[-1]
            )
            print(
                f"Workflow Total: P50={p50:.2f}s, P95={p95:.2f}s, "
                f"min={min(wf_totals):.2f}s, max={max(wf_totals):.2f}s"
            )
        if tokens:
            print(
                f"Total Tokens: min={min(tokens)}, max={max(tokens)}, "
                f"avg={sum(tokens) / len(tokens):.0f}"
            )
        print(
            f"Repair Trigger Rate: {repair_count}/{len(valid)} "
            f"({100 * repair_count / len(valid):.0f}%)"
        )

        # Drop reasons
        all_drop_reasons: dict[str, int] = {}
        all_cdrop_reasons: dict[str, int] = {}
        for m in valid:
            for r, c in m.get("drop_reasons", {}).items():
                all_drop_reasons[r] = all_drop_reasons.get(r, 0) + c
            for r, c in m.get("canonical_drop_reasons", {}).items():
                all_cdrop_reasons[r] = all_cdrop_reasons.get(r, 0) + c

        if all_drop_reasons:
            print("\n-- Drop Reasons (draft level) --")
            for r, c in sorted(all_drop_reasons.items(), key=lambda x: -x[1]):
                print(f"  {r}: {c}")

        if all_cdrop_reasons:
            print("\n-- Canonical Drop Reasons --")
            for r, c in sorted(all_cdrop_reasons.items(), key=lambda x: -x[1]):
                print(f"  {r}: {c}")

        # Anchor kind distribution
        all_anchor_kinds: dict[str, int] = {}
        for m in valid:
            for k, c in m.get("anchor_kind_distribution", {}).items():
                all_anchor_kinds[k] = all_anchor_kinds.get(k, 0) + c
        if all_anchor_kinds:
            total_anchors = sum(all_anchor_kinds.values())
            print(f"\n-- Anchor Kind Distribution ({total_anchors} total) --")
            for k, c in sorted(all_anchor_kinds.items(), key=lambda x: -x[1]):
                print(f"  {k}: {c} ({100*c/total_anchors:.0f}%)")

        # Range warnings
        total_range_warnings = sum(m.get("range_warning_count", 0) for m in valid)
        print("\n-- Range Warnings --")
        print(f"  Total: {total_range_warnings}")
        all_range_codes: dict[str, int] = {}
        for m in valid:
            for code in m.get("range_warning_codes", []):
                all_range_codes[code] = all_range_codes.get(code, 0) + 1
        for code, c in sorted(all_range_codes.items(), key=lambda x: -x[1]):
            print(f"  {code}: {c}")

        # Node timings breakdown
        print("\n-- Node Timings Breakdown --")
        node_keys = [
            "prepare_input",
            "derive_user_config",
            "parallel_agents",
            "normalize_and_ground",
            "repair_agent",
            "project_render_scene",
            "assemble_result",
        ]
        for key in node_keys:
            vals = []
            for m in valid:
                nt = m.get("node_timings", {})
                if nt and key in nt:
                    v = nt[key]
                    if isinstance(v, dict) and "elapsed_s" in v:
                        vals.append(v["elapsed_s"])
                    elif isinstance(v, int | float):
                        vals.append(v)
            if vals:
                vals.sort()
                p50 = vals[len(vals) // 2]
                print(f"  {key}: P50={p50:.2f}s, min={min(vals):.2f}s, max={max(vals):.2f}s")

        # Per-agent timing from node_timings flat agent keys.
        print("\n-- Per-Agent Timing --")
        agent_keys = ["vocabulary_agent", "grammar_agent", "translation_agent"]
        for agent in agent_keys:
            vals = []
            for m in valid:
                nt = m.get("node_timings", {})
                if nt and agent in nt:
                    v = nt[agent]
                    if isinstance(v, int | float):
                        vals.append(v)
            if vals:
                vals.sort()
                p50 = vals[len(vals) // 2]
                print(f"  {agent}: P50={p50:.2f}s, min={min(vals):.2f}s, max={max(vals):.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
