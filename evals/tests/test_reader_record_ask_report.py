"""Tests for the R4-A3 report generator.

Covers the 15 base sections + 4 rework closure sections (16-19) +
sanitization invariants per spec
(`.trae/specs/reader-record-ask-r4-a3-correctness-eval/spec.md` —
Requirement: 交付报告内容 + 报告脱敏与可聚合;
rework spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-
eval-closure/spec.md` — 能力边界 / 覆盖状态 / budget 语义 / thinking 验证).
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.aggregator import (
    AggregatedReport,
    FailureCluster,
    aggregate_results,
)
from claread_eval.reader_record_ask.evaluators.artifact import (
    RawArtifact,
    RawUsage,
)
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.report import (
    REQUIRED_SECTION_HEADERS,
    generate_r4_a3_report,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Dataset,
    ReaderRecordAskR4A3Expected,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Synthetic placeholder UUID — real local Reading Record UUIDs must
# never appear in tracked fixtures (spec: P0 dataset Git governance §7).
_BBC_RECORD_ID = "00000000-0000-4000-8000-000000000000"


def _make_case(
    case_id: str = "bbc-city-enumeration-unknown",
    *,
    source_kind: str = "bbc_record",
    question_category: str = "city_enumeration",
    source_metadata: str = "unknown",
    record_id: str | None = _BBC_RECORD_ID,
) -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind=source_kind,  # type: ignore[arg-type]
        record_id=record_id,
        input_mode="no_selection",
        source_metadata=source_metadata,  # type: ignore[arg-type]
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category=question_category,  # type: ignore[arg-type]
        expected=ReaderRecordAskR4A3Expected(
            expected_entity_set={
                "city": ["Thunder Bay", "纽约", "多伦多"],
            },
            allowed_temporal_claims=[],
            allowed_numerics=["858", "30"],
            allowed_entities_by_type={
                "city": ["Thunder Bay", "纽约", "多伦多"],
            },
            forbidden_answer_patterns=["2025", "2026"],
            answer_language="zh",
            expect_tool_calls="forbidden",
        ),
        tags=["bbc", "city_enumeration", "source_unknown"],
    )


def _make_dataset(case: ReaderRecordAskR4A3Case | None = None) -> (
    ReaderRecordAskR4A3Dataset
):
    if case is None:
        case = _make_case()
    return ReaderRecordAskR4A3Dataset(
        id="reader-record-ask-r4-a3",
        schema_version="r4-a3-dataset-v1",
        description="R4-A3 test dataset",
        case_globs=["cases/*.json"],
        tags=["r4-a3", "test"],
        cases=[case],
    )


def _make_artifact(
    *,
    case_id: str = "bbc-city-enumeration-unknown",
    final_text: str | None = "文章提到了 Thunder Bay、纽约、多伦多。",
    finalized_status: str | None = "ok",
    model_short_name: str | None = "deepseek-chat",
    thinking_enabled: bool = False,
    run_id: str = "phase1-test",
    run_index: int = 0,
    error: str | None = None,
) -> RawArtifact:
    return RawArtifact(
        case_id=case_id,
        run_id=run_id,
        run_index=run_index,
        model_short_name=model_short_name,
        model_route="reader_ask",
        thinking_enabled=thinking_enabled,
        final_text=final_text,
        finalized_status=finalized_status,
        finalized_reason=None,
        response_kind="grounded_answer",
        cited_evidence_handles=["ev-1"],
        resolved_evidence=[],
        all_evidence_observations=[],
        read_range_calls=0,
        search_current_article_calls=0,
        baseline_status="injected",
        baseline_is_complete=True,
        baseline_is_injected=True,
        agent_usage=RawUsage(
            requests=1,
            input_tokens=500,
            output_tokens=200,
        ),
        latency_seconds=2.5,
        envelope_fingerprint="test-fingerprint",
        error=error,
    )


def _make_dim(
    name: str,
    *,
    passed: bool,
    severity: str = "none",
    details: str = "",
) -> EvalDimensionResult:
    return EvalDimensionResult(
        dimension=name,
        passed=passed,
        severity=severity,  # type: ignore[arg-type]
        details=details,
    )


def _make_aggregated_with_clusters() -> AggregatedReport:
    """Build an AggregatedReport with 2 distinct failure clusters."""
    case1 = _make_case("case-A", question_category="city_enumeration")
    case2 = _make_case("case-B", question_category="exercise_one")
    cases_by_id = {c.id: c for c in (case1, case2)}

    # CaseEvalResult is built via aggregate_results so the failure_clusters
    # get populated by the aggregator's pattern extractor.
    from claread_eval.reader_record_ask.evaluators.aggregator import (
        CaseEvalResult,
    )

    cr1 = CaseEvalResult(
        case_id="case-A",
        run_id="phase1-test",
        run_index=0,
        model_short_name="deepseek-chat",
        thinking_enabled=False,
        dimensions=[
            _make_dim(
                "unsupported_temporal_claims",
                passed=False,
                severity="high",
                details="unsupported temporal tokens: ['2025 年']",
            ),
            _make_dim("answer_success", passed=True),
        ],
        latency_seconds=2.0,
        total_tokens=700,
        total_requests=1,
    )
    cr2 = CaseEvalResult(
        case_id="case-B",
        run_id="phase1-test",
        run_index=0,
        model_short_name="deepseek-chat",
        thinking_enabled=False,
        dimensions=[
            _make_dim(
                "instruction_following",
                passed=False,
                severity="high",
                details="expected 1 exercise items, got 5",
            ),
            _make_dim("answer_success", passed=True),
        ],
        latency_seconds=3.0,
        total_tokens=900,
        total_requests=1,
    )
    return aggregate_results([cr1, cr2], cases_by_id)


def _default_report_kwargs(
    *,
    aggregated: AggregatedReport | None = None,
    dataset: ReaderRecordAskR4A3Dataset | None = None,
    artifacts: list[RawArtifact] | None = None,
    real_model_blocked: bool = True,
    verdict: str = "blocked",
    allow_r4_a4: bool = True,
    allow_r4_b1: bool = False,
) -> dict:
    if aggregated is None:
        aggregated = AggregatedReport(
            run_id="phase1-test",
            total_cases=0,
            total_runs=0,
            per_case=[],
            per_dimension={},
            per_config={},
            failure_clusters=[],
        )
    if dataset is None:
        dataset = _make_dataset()
    if artifacts is None:
        artifacts = []
    return dict(
        aggregated=aggregated,
        dataset=dataset,
        artifacts=artifacts,
        start_head="44e4ca9c0318e627217beaed5a2187b8cf0e5558",
        end_head="614020aa531aa3f66028a45dcdcb43406866b07f",
        parallel_dirty=[
            " M apps/web/package.json",
            " M services/api/app/infra/bailian_embedding.py",
        ],
        harness_choice=(
            "B: in-process real-model harness "
            "(services/api/tests/test_reader_record_ask_real_llm_eval.py "
            "直接调用 run_reading_record_ask)"
        ),
        rejected_harness=(
            "A: HTTP adapter via evals/claread_eval/adapter/http_client.py"
        ),
        rejected_reason=(
            "现有 http_client.py 调用 /eval/article-analysis/workflow"
            "（旧 article-analysis 端点），不是 RR Ask SSE 端点。"
        ),
        real_model_blocked=real_model_blocked,
        real_model_block_reason=(
            "no artifacts found; env gate not opened"
            if real_model_blocked
            else None
        ),
        real_model_user_commands=(
            [
                "set CLAREAD_ALLOW_REAL_LLM_TESTS=1",
                "set CLAREAD_R4_A3_RUN=1",
                "set CLAREAD_REAL_LLM_MODEL=deepseek-chat",
                "uv run pytest tests/test_reader_record_ask_real_llm_eval.py "
                "-v -m real_llm -k phase1",
            ]
            if real_model_blocked
            else None
        ),
        deterministic_tests_passed=True,
        deterministic_tests_summary=(
            "evals/tests/test_reader_record_ask_dataset.py: 10 passed; "
            "evals/tests/test_reader_record_ask_eval_*.py: 70 passed; "
            "ruff All checks passed."
        ),
        verdict=verdict,
        allow_r4_a4=allow_r4_a4,
        allow_r4_b1=allow_r4_b1,
        run_metadata=None,
    )


# ---------------------------------------------------------------------------
# Section presence
# ---------------------------------------------------------------------------


def test_report_contains_all_required_sections() -> None:
    """Report markdown contains all required section headers (15 + 4 rework)."""
    report = generate_r4_a3_report(**_default_report_kwargs())
    for header in REQUIRED_SECTION_HEADERS:
        assert header in report, (
            f"missing required section header: {header!r}\n"
            f"--- report snippet ---\n{report[:1000]}"
        )


def test_report_contains_title_and_dataset_metadata() -> None:
    """Report has a title block + dataset id."""
    report = generate_r4_a3_report(**_default_report_kwargs())
    assert "# TMP — Reader Record Ask R4-A3 评测报告" in report
    assert "dataset: `reader-record-ask-r4-a3`" in report
    assert "verdict: **blocked**" in report


def test_report_contains_rework_closure_sections() -> None:
    """Sections 16-19 (rework closure) contain expected key content."""
    report = generate_r4_a3_report(**_default_report_kwargs())
    # Section 16: 能力边界声明
    assert "## 16. 能力边界声明" in report
    assert "能验证" in report
    assert "不能验证" in report
    assert "不引入假装确定性的 LLM judge" in report
    # Section 17: 真实覆盖状态
    assert "## 17. 真实覆盖状态" in report
    assert "offline_only" in report
    assert "real_phase1" in report
    assert "运行时覆盖状态" in report
    # Section 18: request/token budget 真实语义
    assert "## 18. request/token budget 真实语义" in report
    assert "BudgetedUsageModel" in report
    assert "BudgetExhaustedError" in report
    assert "budget_exhausted" in report
    # Section 19: thinking 验证方式
    assert "## 19. thinking 验证方式" in report
    assert "thinking_enabled" in report
    assert "Phase 1" in report and "Phase 2" in report and "Phase 3" in report


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


def test_report_sanitized_no_bbc_body() -> None:
    """Report must not contain long BBC source prose (≥200 contiguous chars).

    We inject a long BBC-body-like chunk via ``run_metadata`` (the only
    string-typed channel the report consumes besides dataset fields) and
    verify the sanitization layer truncates it to ≤200 chars + marker.
    """
    # Synthetic prose (NOT real BBC body) — used to verify the report's
    # long-prose sanitization guard truncates ≥200 contiguous chars.
    long_bbc_chunk = (
        "SYNTHETIC_TEST_PROSE_PADDING_USED_ONLY_TO_VERIFY_THE_REPORT_"
        "SANITIZATION_LAYER_TRUNCATES_LONG_CONTIGUOUS_STRINGS_AS_REQUIRED_"
        "BY_THE_P0_DATASET_GIT_GOVERNANCE_SPEC_SECTION_7_WHICH_FORBIDS_"
        "REAL_BBC_BODY_CONTENT_FROM_TRACKED_FIXURES_EXTRA_PADDING_FOLLOWS_"
        + "a" * 80
    )
    assert len(long_bbc_chunk) >= 200
    kwargs = _default_report_kwargs()
    kwargs["run_metadata"] = {
        "model_route": "reader_ask",
        "leaked_bbc_body": long_bbc_chunk,
    }
    report = generate_r4_a3_report(**kwargs)
    # The full long chunk must not appear verbatim.
    assert long_bbc_chunk not in report
    # Truncation marker must appear (sanitization truncated the value).
    assert "[truncated]" in report
    # And no 200+ char ASCII prose run starting with the BBC prefix.
    import re

    matches = re.findall(r"[\x20-\x7E]{200,}", report)
    for match in matches:
        assert not match.startswith(long_bbc_chunk[:50]), (
            f"report contains BBC prose chunk prefix: {match[:80]!r}"
        )


def test_report_sanitized_no_reasoning_content_leak() -> None:
    """Report must not leak ``reasoning_content`` VALUES from run_metadata.

    The declarative sections (16 能力边界, 18 budget 语义) legitimately
    mention ``reasoning_content`` as a field name concept — e.g.
    "不记录 reasoning_content". This is NOT a leak.

    A leak would be: the ``reasoning_content`` key or its VALUE appearing
    in the run_metadata appendix. We inject a ``reasoning_content`` key +
    value via ``run_metadata`` and verify the sanitization layer drops
    them from the appendix.
    """
    kwargs = _default_report_kwargs()
    kwargs["run_metadata"] = {
        "model_route": "reader_ask",
        "reasoning_content": "some leaked chain-of-thought text",
        "other_notes": "harmless value with reasoning_content mention",
    }
    report = generate_r4_a3_report(**kwargs)
    # The leaked VALUE must not appear anywhere in the report.
    assert "some leaked chain-of-thought text" not in report, (
        "reasoning_content value must be stripped from run_metadata"
    )
    # The other_notes value (which contains the substring) must also be
    # stripped — the sanitization drops any string value containing
    # "reasoning_content".
    assert "harmless value with" not in report, (
        "other_notes value containing reasoning_content substring must be stripped"
    )
    # The run_metadata appendix must NOT contain the reasoning_content key.
    # (Find the appendix section and check it doesn't have the key.)
    if "## 附录: run_metadata" in report:
        appendix_start = report.index("## 附录: run_metadata")
        appendix = report[appendix_start:]
        assert '"reasoning_content"' not in appendix, (
            "run_metadata appendix must not contain reasoning_content key"
        )


def test_report_sanitized_no_api_key() -> None:
    """Report must not contain 'sk-' or 'api_key=' patterns."""
    kwargs = _default_report_kwargs()
    # Inject API key via run_metadata to verify sanitization path.
    kwargs["run_metadata"] = {
        "model_route": "reader_ask",
        "leaked_key": "sk-test-leaked-key-1234567890",
        "leaked_env": "OPENAI_API_KEY=sk-proj-leaked",
    }
    report = generate_r4_a3_report(**kwargs)
    assert "sk-test-leaked-key-1234567890" not in report
    assert "sk-proj-leaked" not in report
    assert "api_key=" not in report.lower()


# ---------------------------------------------------------------------------
# Blocked verdict when no artifacts
# ---------------------------------------------------------------------------


def test_report_blocked_verdict_when_no_artifacts() -> None:
    """Empty artifacts → real_model_blocked=True → verdict='blocked'."""
    report = generate_r4_a3_report(**_default_report_kwargs())
    assert "BLOCKED" in report
    assert "verdict: **blocked**" in report
    assert "real model run was not executed; results are not fabricated" in report


# ---------------------------------------------------------------------------
# Failure clusters rendering
# ---------------------------------------------------------------------------


def test_report_failure_clusters_rendered() -> None:
    """AggregatedReport with 2 failure clusters renders them in §10."""
    aggregated = _make_aggregated_with_clusters()
    dataset = _make_dataset()
    report = generate_r4_a3_report(
        **_default_report_kwargs(
            aggregated=aggregated,
            dataset=dataset,
            artifacts=[],
            real_model_blocked=False,
            verdict="rework",
        )
    )
    assert "## 10. 明确失败簇" in report
    # Both failure patterns should be rendered.
    assert "2025-year-hallucination" in report
    assert "count-mismatch" in report
    # case ids should appear.
    assert "`case-A`" in report
    assert "`case-B`" in report
    # And the cluster dimensions.
    assert "unsupported_temporal_claims" in report
    assert "instruction_following" in report


def test_report_failure_clusters_blocked_falls_back_to_spec_anticipated() -> None:
    """When real_model_blocked=True, §10 lists spec-anticipated clusters."""
    report = generate_r4_a3_report(**_default_report_kwargs())
    assert "## 10. 明确失败簇" in report
    assert "N/A (blocked / 无真实运行数据)" in report
    assert "2025-year-hallucination" in report
    assert "missing-Thunder Bay" in report


# ---------------------------------------------------------------------------
# R4-A4 candidate suggestions
# ---------------------------------------------------------------------------


def test_report_r4_a4_candidates_present() -> None:
    """§11 R4-A4 candidate suggestions are present and marked not-implement."""
    report = generate_r4_a3_report(**_default_report_kwargs())
    assert "## 11. R4-A4 候选修复建议" in report
    assert "不实施" in report
    assert "待 R4-A4 立项" in report
    # At least one candidate direction is named.
    assert "temporal claim policy" in report
    assert "instruction count validator" in report


# ---------------------------------------------------------------------------
# Verdict + next-step decision
# ---------------------------------------------------------------------------


def test_report_verdict_rework_when_artifacts_have_failures() -> None:
    """When artifacts exist with high-severity failures, verdict is rework."""
    aggregated = _make_aggregated_with_clusters()
    artifact = _make_artifact()
    report = generate_r4_a3_report(
        **_default_report_kwargs(
            aggregated=aggregated,
            artifacts=[artifact],
            real_model_blocked=False,
            verdict="rework",
            allow_r4_a4=True,
            allow_r4_b1=False,
        )
    )
    assert "verdict: **rework**" in report
    # R4-A4 allowed (立项) but R4-B1 deferred.
    assert "**R4-A4**: 允许" in report
    assert "**R4-B1**: 暂不允许" in report


def test_report_tracker_section_points_to_tracker_file() -> None:
    """§14 references the tracker file path."""
    report = generate_r4_a3_report(**_default_report_kwargs())
    assert "## 14. R4 tracker 更新" in report
    assert (
        "docs/tmp/reader-orchestration/"
        "TMP-reader-record-ask-r4-product-ready-tracker-2026-07-17.md"
    ) in report


def test_report_no_commit_section_present() -> None:
    """§15 declares no git commit/reset/restore/checkout/stash."""
    report = generate_r4_a3_report(**_default_report_kwargs())
    assert "## 15. 未 commit" in report
    assert "git commit" in report
    assert "git reset" in report
    assert "git checkout" in report
    assert "git stash" in report


# ---------------------------------------------------------------------------
# FailureCluster type itself
# ---------------------------------------------------------------------------


def test_failure_cluster_field_shape() -> None:
    """FailureCluster carries the documented fields."""
    cluster = FailureCluster(
        dimension="unsupported_temporal_claims",
        question_category="city_enumeration",
        failure_pattern="2025-year-hallucination",
        failed_count=3,
        total_count=3,
        case_ids=["case-A", "case-B", "case-C"],
    )
    assert cluster.dimension == "unsupported_temporal_claims"
    assert cluster.failed_count == 3
    assert cluster.total_count == 3
    assert cluster.case_ids == ["case-A", "case-B", "case-C"]
