from __future__ import annotations

import time
from typing import Any

from claread_eval.adapter.protocol import ArticleAnalysisAdapterClient
from claread_eval.graders.base import BaseGrader
from claread_eval.graders.schema_presence import SchemaPresenceGrader
from claread_eval.graders.status_error import StatusErrorGrader
from claread_eval.graders.translation_coverage import TranslationCoverageGrader
from claread_eval.graders.warning_drop_summary import WarningDropSummaryGrader
from claread_eval.schemas.dataset import EvalCase, EvalDataset
from claread_eval.schemas.grader import GraderResult, GraderSeverity, GraderVerdict
from claread_eval.schemas.report import CaseSummary, EvalReport
from claread_eval.schemas.run import (
    DropLogEntry,
    EvalCaseArtifact,
    EvalRunConfig,
    ModelIdentity,
    PromptIdentity,
    SchemaIdentity,
    UsageSummary,
    WarningEntry,
    WorkflowIdentity,
)
from claread_eval.writer.artifact_writer import init_run_dir, write_case_artifact, write_report

DEFAULT_GRADERS: list[BaseGrader] = [
    SchemaPresenceGrader(),
    StatusErrorGrader(),
    TranslationCoverageGrader(),
    WarningDropSummaryGrader(),
]


async def run_eval(
    dataset: EvalDataset,
    cases: list[EvalCase],
    run_config: EvalRunConfig,
    adapter: ArticleAnalysisAdapterClient,
    runs_root: str | Any = "runs",
    graders: list[BaseGrader] | None = None,
    adapter_run_config: dict[str, Any] | None = None,
) -> EvalReport:
    if graders is None:
        graders = DEFAULT_GRADERS

    run_dir = init_run_dir(runs_root, run_config)

    all_grader_results: list[GraderResult] = []
    case_summaries: list[CaseSummary] = []

    for case in cases:
        artifact = await _run_single_case(case, run_config, adapter, adapter_run_config)
        case_grader_results = [g.grade(case, artifact) for g in graders]
        artifact.grader_results = [r.model_dump(mode="json") for r in case_grader_results]
        all_grader_results.extend(case_grader_results)

        write_case_artifact(run_dir, artifact)

        summary = _build_case_summary(case.id, case_grader_results, artifact.error)
        case_summaries.append(summary)

    report = _build_report(run_config, case_summaries, all_grader_results)
    write_report(run_dir, report)

    return report


async def _run_single_case(
    case: EvalCase,
    run_config: EvalRunConfig,
    adapter: ArticleAnalysisAdapterClient,
    adapter_run_config: dict[str, Any] | None = None,
) -> EvalCaseArtifact:
    start = time.monotonic()
    adapter_payload = {
        **run_config.model_dump(mode="json"),
        **(adapter_run_config or {}),
    }
    try:
        result = await adapter.analyze(case, adapter_payload)
    except Exception as exc:
        elapsed = time.monotonic() - start
        return EvalCaseArtifact(
            case_id=case.id,
            run_id=run_config.run_id,
            adapter_status="failed",
            input_snapshot=case.model_dump(mode="json"),
            run_config_snapshot=run_config.model_dump(mode="json"),
            prompt_identity=PromptIdentity(
                prompt_version=run_config.prompt_version,
                prompt_variant_id=run_config.prompt_variant_id,
            ),
            error={"code": type(exc).__name__, "message": str(exc)},
            latency_seconds=round(elapsed, 3),
        )

    elapsed = time.monotonic() - start
    raw_status = result.get("status") or ("succeeded" if result.get("render_scene") else "failed")
    status = raw_status if raw_status in {"succeeded", "failed", "timeout"} else "failed"
    error_raw = result.get("error")
    render_scene = result.get("render_scene") or {}
    runtime_raw = result.get("runtime_summary") or {}
    usage_raw = result.get("usage_summary") or runtime_raw
    model_id_raw = result.get("model_identity", {})
    drop_log_raw = result.get("drop_log", [])

    translations = render_scene.get("translations", [])
    inline_marks = render_scene.get("inline_marks", [])
    sentence_entries = render_scene.get("sentence_entries", [])
    warnings_raw = render_scene.get("warnings", [])

    aggregate = usage_raw.get("aggregate", {}) if isinstance(usage_raw, dict) else {}
    per_agent = usage_raw.get("per_agent", {}) if isinstance(usage_raw, dict) else {}
    error = error_raw if isinstance(error_raw, dict) else None

    return EvalCaseArtifact(
        case_id=case.id,
        run_id=run_config.run_id,
        adapter_status=status,
        input_snapshot=case.model_dump(mode="json"),
        run_config_snapshot=run_config.model_dump(mode="json"),
        workflow_identity=WorkflowIdentity(**result.get("workflow_identity", {})),
        schema_identity=SchemaIdentity(**result.get("schema_identity", {})),
        prompt_identity=PromptIdentity(
            **{
                **result.get("prompt_identity", {}),
                "prompt_variant_id": (
                    result.get("prompt_identity", {}).get("prompt_variant_id")
                    or run_config.prompt_variant_id
                ),
            }
        ),
        output=render_scene,
        user_facing_state=render_scene.get("user_facing_state"),
        translations=translations,
        inline_marks=inline_marks,
        sentence_entries=sentence_entries,
        warnings=[WarningEntry(**w) for w in warnings_raw],
        drop_log=[DropLogEntry(**d) for d in drop_log_raw],
        preprocess_summary=result.get("preprocess_summary"),
        normalize_summary=result.get("normalize_summary"),
        drop_log_summary=result.get("drop_log_summary"),
        runtime_summary=runtime_raw if isinstance(runtime_raw, dict) else None,
        rag_debug=result.get("rag_debug"),
        trace_refs=result.get("trace_refs"),
        usage_summary=UsageSummary(
            total_tokens=aggregate.get("total_tokens", 0),
            per_agent=per_agent,
        ),
        model_identity=ModelIdentity(**model_id_raw) if model_id_raw else ModelIdentity(),
        error=error,
        timeout=status == "timeout",
        latency_seconds=round(elapsed, 3),
    )


def _build_case_summary(
    case_id: str,
    results: list[GraderResult],
    error: dict[str, Any] | None,
) -> CaseSummary:
    hard_failures = sum(
        1 for r in results if r.verdict == GraderVerdict.FAIL and r.severity == GraderSeverity.HARD
    )
    soft_failures = sum(
        1 for r in results if r.verdict == GraderVerdict.FAIL and r.severity == GraderSeverity.SOFT
    )

    if error:
        verdict = GraderVerdict.ERROR
    elif hard_failures > 0:
        verdict = GraderVerdict.FAIL
    else:
        verdict = GraderVerdict.PASS

    return CaseSummary(
        case_id=case_id,
        verdict=verdict,
        hard_failures=hard_failures,
        soft_failures=soft_failures,
        error=(error or {}).get("message") if isinstance(error, dict) else None,
    )


def _build_report(
    run_config: EvalRunConfig,
    case_summaries: list[CaseSummary],
    grader_results: list[GraderResult],
) -> EvalReport:
    total = len(case_summaries)
    passed = sum(1 for cs in case_summaries if cs.verdict == GraderVerdict.PASS)
    failed = sum(1 for cs in case_summaries if cs.verdict == GraderVerdict.FAIL)
    skipped = sum(1 for cs in case_summaries if cs.verdict == GraderVerdict.SKIP)
    errored = sum(1 for cs in case_summaries if cs.verdict == GraderVerdict.ERROR)

    hard_failure_ids = [
        cs.case_id
        for cs in case_summaries
        if cs.hard_failures > 0
    ]
    soft_failure_ids = [
        cs.case_id
        for cs in case_summaries
        if cs.soft_failures > 0 and cs.hard_failures == 0
    ]

    return EvalReport(
        run_id=run_config.run_id,
        dataset_id=run_config.dataset_id,
        total_cases=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        errored=errored,
        hard_failure_case_ids=hard_failure_ids,
        soft_failure_case_ids=soft_failure_ids,
        case_summaries=case_summaries,
    )
