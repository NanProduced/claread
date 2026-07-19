from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.aggregator import (
    AggregatedReport,
    CaseEvalResult,
    aggregate_results,
)
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)


def _make_case(case_id: str, qcat: str = "city_enumeration") -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category=qcat,  # type: ignore[arg-type]
        expected=ReaderRecordAskR4A3Expected(),
    )


def _dim(
    name: str,
    *,
    passed: bool,
    severity: str = "none",
    details: str = "",
    llm_judge_used: bool = False,
    llm_judge_note: str | None = None,
) -> EvalDimensionResult:
    return EvalDimensionResult(
        dimension=name,
        passed=passed,
        severity=severity,  # type: ignore[arg-type]
        details=details,
        llm_judge_used=llm_judge_used,
        llm_judge_note=llm_judge_note,
    )


def _case_result(
    *,
    case_id: str,
    run_id: str = "run-1",
    model: str = "flash",
    thinking: bool = False,
    dimensions: list[EvalDimensionResult],
    latency: float | None = 12.0,
    tokens: int | None = 1500,
    requests: int | None = 3,
) -> CaseEvalResult:
    return CaseEvalResult(
        case_id=case_id,
        run_id=run_id,
        run_index=0,
        model_short_name=model,
        thinking_enabled=thinking,
        dimensions=dimensions,
        latency_seconds=latency,
        total_tokens=tokens,
        total_requests=requests,
    )


def test_failure_clusters_two_temporal_one_completeness() -> None:
    """3 cases: 2 unsupported_temporal_claims failures + 1
    exhaustive_completeness failure. Verify clusters group correctly.
    """
    case1 = _make_case("c1")
    case2 = _make_case("c2")
    case3 = _make_case("c3")
    cases_by_id = {c.id: c for c in (case1, case2, case3)}

    cr1 = _case_result(
        case_id="c1",
        dimensions=[
            _dim(
                "unsupported_temporal_claims",
                passed=False,
                severity="high",
                details="unsupported temporal tokens: ['2025 年']",
            ),
            _dim("exhaustive_completeness", passed=True),
        ],
    )
    cr2 = _case_result(
        case_id="c2",
        dimensions=[
            _dim(
                "unsupported_temporal_claims",
                passed=False,
                severity="high",
                details="unsupported temporal tokens: ['2025 年']",
            ),
            _dim("exhaustive_completeness", passed=True),
        ],
    )
    cr3 = _case_result(
        case_id="c3",
        dimensions=[
            _dim("unsupported_temporal_claims", passed=True),
            _dim(
                "exhaustive_completeness",
                passed=False,
                severity="high",
                details="city recall=0.80 missing=['Thunder Bay']",
            ),
        ],
    )

    report = aggregate_results([cr1, cr2, cr3], cases_by_id)

    assert isinstance(report, AggregatedReport)
    assert report.total_cases == 3
    assert report.total_runs == 3

    # Two clusters expected: temporal (2 failed) + completeness (1 failed)
    assert len(report.failure_clusters) == 2

    temporal_cluster = next(
        c for c in report.failure_clusters
        if c.dimension == "unsupported_temporal_claims"
    )
    assert temporal_cluster.question_category == "city_enumeration"
    assert temporal_cluster.failure_pattern == "2025-year-hallucination"
    assert temporal_cluster.failed_count == 2
    assert temporal_cluster.total_count == 3
    assert set(temporal_cluster.case_ids) == {"c1", "c2"}

    completeness_cluster = next(
        c for c in report.failure_clusters
        if c.dimension == "exhaustive_completeness"
    )
    assert completeness_cluster.failure_pattern == "missing-Thunder Bay"
    assert completeness_cluster.failed_count == 1
    assert completeness_cluster.total_count == 3
    assert completeness_cluster.case_ids == ["c3"]


def test_deterministic_failure_not_overridden_by_llm_judge() -> None:
    """A dimension with passed=False + llm_judge_used=True + positive
    note MUST still be counted as failed in per_dimension and appear in
    failure_clusters. The LLM judge cannot flip a deterministic failure.
    """
    case = _make_case("c-judge")
    cases_by_id = {case.id: case}

    cr = _case_result(
        case_id="c-judge",
        dimensions=[
            _dim(
                "entity_precision",
                passed=False,
                severity="high",
                details="type confusion: entity '纽约州西部' (type='region') "
                "appears in answer but is not in 'city' allowed list",
                llm_judge_used=True,
                llm_judge_note="all entities look reasonable to me",
            ),
        ],
    )

    report = aggregate_results([cr], cases_by_id)

    # per_dimension counts it as FAILED despite the positive llm note.
    ep_bucket = report.per_dimension["entity_precision"]
    assert ep_bucket["failed"] == 1
    assert ep_bucket["passed"] == 0
    assert ep_bucket["total"] == 1

    # failure_clusters includes it.
    assert len(report.failure_clusters) == 1
    cluster = report.failure_clusters[0]
    assert cluster.dimension == "entity_precision"
    assert cluster.failed_count == 1


def test_per_dimension_counts() -> None:
    case = _make_case("c-count")
    cases_by_id = {case.id: case}

    cr1 = _case_result(
        case_id="c-count",
        dimensions=[
            _dim("answer_success", passed=True),
            _dim("context_support", passed=True),
        ],
    )
    cr2 = _case_result(
        case_id="c-count",
        run_id="run-2",
        dimensions=[
            _dim("answer_success", passed=False, severity="high", details="empty"),
            _dim("context_support", passed=True),
        ],
    )

    report = aggregate_results([cr1, cr2], cases_by_id)

    assert report.per_dimension["answer_success"] == {
        "passed": 1,
        "failed": 1,
        "total": 2,
    }
    assert report.per_dimension["context_support"] == {
        "passed": 2,
        "failed": 0,
        "total": 2,
    }


def test_per_config_metrics() -> None:
    case = _make_case("c-config")
    cases_by_id = {case.id: case}

    # Config A: flash|thinking=False, 2 runs, 1 pass
    cr1 = _case_result(
        case_id="c-config",
        model="flash",
        thinking=False,
        latency=10.0,
        tokens=1000,
        requests=2,
        dimensions=[
            _dim("answer_success", passed=True),
            _dim("unsupported_temporal_claims", passed=True),
            _dim("exhaustive_completeness", passed=True),
            _dim("instruction_following", passed=True),
        ],
    )
    cr2 = _case_result(
        case_id="c-config",
        run_id="run-2",
        model="flash",
        thinking=False,
        latency=20.0,
        tokens=2000,
        requests=4,
        dimensions=[
            _dim("answer_success", passed=False, severity="high", details="empty"),
            _dim("unsupported_temporal_claims", passed=False, severity="high",
                 details="unsupported temporal tokens: ['2025']"),
            _dim("exhaustive_completeness", passed=False, severity="high",
                 details="city recall=0.50 missing=['Thunder Bay']"),
            _dim("instruction_following", passed=True),
        ],
    )
    # Config B: pro|thinking=True, 1 run, 1 pass
    cr3 = _case_result(
        case_id="c-config",
        run_id="run-3",
        model="pro",
        thinking=True,
        latency=30.0,
        tokens=3000,
        requests=5,
        dimensions=[
            _dim("answer_success", passed=True),
            _dim("unsupported_temporal_claims", passed=True),
            _dim("exhaustive_completeness", passed=True),
            _dim("instruction_following", passed=True),
        ],
    )

    report = aggregate_results([cr1, cr2, cr3], cases_by_id)

    assert "flash|thinking=False" in report.per_config
    assert "pro|thinking=True" in report.per_config

    flash_cfg = report.per_config["flash|thinking=False"]
    # R4-A4-0 (Task 5): ``total_runs`` MUST be explicitly written.
    assert flash_cfg["total_runs"] == 2
    assert flash_cfg["pass_rate"] == 0.5  # 1 of 2 runs fully passed
    assert flash_cfg["avg_latency"] == 15.0  # (10+20)/2
    assert flash_cfg["avg_tokens"] == 1500.0  # (1000+2000)/2
    assert flash_cfg["total_requests"] == 6  # 2+4
    assert flash_cfg["unsupported_claim_count"] == 1
    assert flash_cfg["completeness_recall_avg"] == 0.5
    assert flash_cfg["instruction_following_rate"] == 1.0

    pro_cfg = report.per_config["pro|thinking=True"]
    assert pro_cfg["total_runs"] == 1
    assert pro_cfg["pass_rate"] == 1.0
    assert pro_cfg["unsupported_claim_count"] == 0
    assert pro_cfg["completeness_recall_avg"] == 0.0  # no failure → no recall parsed


def test_empty_input_produces_empty_report() -> None:
    report = aggregate_results([], {})
    assert report.total_cases == 0
    assert report.total_runs == 0
    assert report.per_case == []
    assert report.per_dimension == {}
    assert report.per_config == {}
    assert report.failure_clusters == []
