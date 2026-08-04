"""Tests for PhasePlanner — explicit case manifest + fixed repetitions +
evaluator-based failure selection + budget stop.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirements: PhasePlanner 深模块 + 固定重复（P0-2, P0-5）, evaluator-based
Phase 2/3 失败选择（P0-3）.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import (
    RawArtifact,
    RawUsage,
)
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.phase_planner import (
    DEFAULT_PHASE1_REPETITIONS,
    MAX_PHASE1_INDEPENDENT_RUNS,
    PHASE_TAG_OFFLINE_ONLY,
    PHASE_TAG_PHASE2_CANDIDATE,
    PHASE_TAG_REAL_PHASE1,
    BudgetStopResult,
    PhasePlanner,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Dataset,
    ReaderRecordAskR4A3Expected,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_case(
    *,
    case_id: str,
    question_category: str = "main_idea",
    phase_tags: list[str] | None = None,
    source_metadata: str = "unknown",
) -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind="bbc_record",
        record_id="bbc-test-001",
        article_text=None,
        article_title=None,
        input_mode="no_selection",
        selection=None,
        rag_mode="off",
        source_metadata=source_metadata,  # type: ignore[arg-type]
        baseline_mode="complete",
        question="测试问题。",
        question_category=question_category,  # type: ignore[arg-type]
        expected=ReaderRecordAskR4A3Expected(),
        phase_tags=phase_tags or [],
    )


def _make_dataset(cases: list[ReaderRecordAskR4A3Case]) -> ReaderRecordAskR4A3Dataset:
    return ReaderRecordAskR4A3Dataset(cases=cases)


def _make_dim(dimension: str, passed: bool) -> EvalDimensionResult:
    return EvalDimensionResult(
        dimension=dimension,
        passed=passed,
        severity="none" if passed else "high",
        details=f"{dimension}: {'pass' if passed else 'fail'}",
    )


def _make_artifact(case_id: str, run_index: int = 0) -> RawArtifact:
    return RawArtifact(
        case_id=case_id,
        run_id="phase1-test",
        run_index=run_index,
        model_short_name="deepseek-v4",
        model_route="deepseek",
        thinking_enabled=False,
        final_text="测试回答。",
        finalized_status="ok",
        latency_seconds=1.0,
        agent_usage=RawUsage(requests=1, input_tokens=10, output_tokens=20),
    )


# ---------------------------------------------------------------------------
# Construction & validation
# ---------------------------------------------------------------------------


def test_default_repetitions_is_3() -> None:
    assert DEFAULT_PHASE1_REPETITIONS == 3


def test_max_phase1_independent_runs_is_30() -> None:
    assert MAX_PHASE1_INDEPENDENT_RUNS == 30


def test_phase1_default_repetitions(tmp_path: object) -> None:
    dataset = _make_dataset([_make_case(case_id="a", phase_tags=[PHASE_TAG_REAL_PHASE1])])
    planner = PhasePlanner(dataset=dataset, phase=1)
    assert planner.repetitions == 3
    assert planner.phase == 1


def test_phase1_custom_repetitions() -> None:
    dataset = _make_dataset([_make_case(case_id="a", phase_tags=[PHASE_TAG_REAL_PHASE1])])
    planner = PhasePlanner(dataset=dataset, phase=1, repetitions=5)
    assert planner.repetitions == 5


def test_invalid_phase_raises() -> None:
    dataset = _make_dataset([])
    try:
        PhasePlanner(dataset=dataset, phase=4)
    except ValueError as exc:
        assert "phase" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for invalid phase")


def test_invalid_repetitions_raises() -> None:
    dataset = _make_dataset([])
    try:
        PhasePlanner(dataset=dataset, phase=1, repetitions=0)
    except ValueError as exc:
        assert "repetitions" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for invalid repetitions")


def test_phase2_requires_prior_eval_results() -> None:
    dataset = _make_dataset([])
    try:
        PhasePlanner(dataset=dataset, phase=2)
    except ValueError as exc:
        assert "prior_eval_results" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for missing prior_eval_results")


# ---------------------------------------------------------------------------
# Phase 1: explicit case manifest (P0-5)
# ---------------------------------------------------------------------------


def test_phase1_selects_only_real_phase1_tagged_cases() -> None:
    dataset = _make_dataset(
        [
            _make_case(case_id="a", phase_tags=[PHASE_TAG_REAL_PHASE1]),
            _make_case(case_id="b", phase_tags=[]),  # no tag — skipped
            _make_case(case_id="c", phase_tags=[PHASE_TAG_REAL_PHASE1]),
        ]
    )
    planner = PhasePlanner(dataset=dataset, phase=1)
    assert [c.id for c in planner.cases_to_run] == ["a", "c"]


def test_phase1_excludes_offline_only_cases() -> None:
    """``known_bbc`` cases tagged ``offline_only`` must NOT be selected
    for real-model runs (spec: "known cases 应标记为 offline_only /
    future R4-A4 contract，或从真实运行集合移除").
    """
    dataset = _make_dataset(
        [
            _make_case(case_id="a", phase_tags=[PHASE_TAG_REAL_PHASE1]),
            _make_case(
                case_id="b",
                phase_tags=[PHASE_TAG_REAL_PHASE1, PHASE_TAG_OFFLINE_ONLY],
                source_metadata="known_bbc",
            ),
            _make_case(case_id="c", phase_tags=[PHASE_TAG_REAL_PHASE1]),
        ]
    )
    planner = PhasePlanner(dataset=dataset, phase=1)
    assert [c.id for c in planner.cases_to_run] == ["a", "c"]


def test_phase1_preserves_dataset_order() -> None:
    dataset = _make_dataset(
        [
            _make_case(case_id="z", phase_tags=[PHASE_TAG_REAL_PHASE1]),
            _make_case(case_id="a", phase_tags=[PHASE_TAG_REAL_PHASE1]),
            _make_case(case_id="m", phase_tags=[PHASE_TAG_REAL_PHASE1]),
        ]
    )
    planner = PhasePlanner(dataset=dataset, phase=1)
    assert [c.id for c in planner.cases_to_run] == ["z", "a", "m"]


# ---------------------------------------------------------------------------
# Phase 1: independent run cap (P0-2)
# ---------------------------------------------------------------------------


def test_phase1_independent_run_cap_truncates_cases() -> None:
    """When ``cases * repetitions > max_independent_runs``, the planner
    truncates and records a BudgetStopResult.
    """
    # 5 cases × 3 reps = 15 > cap of 12.
    cases = [
        _make_case(case_id=f"c{i}", phase_tags=[PHASE_TAG_REAL_PHASE1])
        for i in range(5)
    ]
    dataset = _make_dataset(cases)
    planner = PhasePlanner(
        dataset=dataset,
        phase=1,
        repetitions=3,
        max_independent_runs=12,
    )
    selected = planner.cases_to_run
    # 4 cases × 3 reps = 12 (cap met); 5th case truncated.
    assert len(selected) == 4
    assert [c.id for c in selected] == ["c0", "c1", "c2", "c3"]

    stop = planner.budget_stop_result
    assert stop is not None
    assert stop.budget_exhausted is True
    assert "c4" in stop.remaining_cases
    assert stop.remaining_run_indices["c4"] == [0, 1, 2]
    assert stop.stop_reason == "phase1_independent_run_cap"


def test_phase1_no_truncation_when_under_cap() -> None:
    cases = [
        _make_case(case_id=f"c{i}", phase_tags=[PHASE_TAG_REAL_PHASE1])
        for i in range(3)
    ]
    dataset = _make_dataset(cases)
    planner = PhasePlanner(dataset=dataset, phase=1, repetitions=3)
    # 3 cases × 3 reps = 9 << 30 (default cap).
    selected = planner.cases_to_run
    assert len(selected) == 3
    assert planner.budget_stop_result is None


# ---------------------------------------------------------------------------
# Phase 2: evaluator-based failure selection (P0-3)
# ---------------------------------------------------------------------------


def test_phase2_selects_cases_with_content_failure() -> None:
    """A case whose prior evaluator results include a content-quality
    failure (e.g. ``unsupported_temporal_claims`` failed due to ``2025``)
    must be selected for Phase 2 — even if ``finalized_status='ok'``.
    """
    dataset = _make_dataset(
        [
            _make_case(
                case_id="bbc-2025",
                question_category="city_enumeration",
                phase_tags=[PHASE_TAG_REAL_PHASE1, PHASE_TAG_PHASE2_CANDIDATE],
            ),
            _make_case(
                case_id="bbc-clean",
                question_category="main_idea",
                phase_tags=[PHASE_TAG_REAL_PHASE1],
            ),
        ]
    )
    prior_evals = {
        "bbc-2025": [[
            _make_dim("answer_success", True),
            _make_dim("unsupported_temporal_claims", False),  # 2025 leaked
            _make_dim("usage_observability", True),
        ]],
        "bbc-clean": [[
            _make_dim("answer_success", True),
            _make_dim("unsupported_temporal_claims", True),
            _make_dim("usage_observability", True),
        ]],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results=prior_evals,
    )
    assert [c.id for c in planner.cases_to_run] == ["bbc-2025"]


def test_phase2_does_not_select_usage_gap_only_cases() -> None:
    """Spec: "默认不要仅因 usage 缺失升级模型".

    A case whose ONLY failing dimension is ``usage_observability`` must
    NOT be selected for Phase 2.
    """
    dataset = _make_dataset(
        [
            _make_case(
                case_id="bbc-usage-gap",
                question_category="main_idea",
                phase_tags=[PHASE_TAG_REAL_PHASE1],
            ),
        ]
    )
    prior_evals = {
        "bbc-usage-gap": [[
            _make_dim("answer_success", True),
            _make_dim("unsupported_temporal_claims", True),
            _make_dim("usage_observability", False),  # only failure
        ]],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results=prior_evals,
    )
    assert planner.cases_to_run == []


def test_phase2_selects_terminal_failures_via_answer_success() -> None:
    """Terminal failures (finalized_status != ok, empty final_text) are
    captured by ``answer_success.passed=False``, which is a content
    failure → selected for Phase 2.
    """
    dataset = _make_dataset(
        [
            _make_case(
                case_id="bbc-terminal",
                question_category="main_idea",
                phase_tags=[PHASE_TAG_REAL_PHASE1],
            ),
        ]
    )
    prior_evals = {
        "bbc-terminal": [[
            _make_dim("answer_success", False),  # terminal failure
            _make_dim("usage_observability", True),
        ]],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results=prior_evals,
    )
    assert [c.id for c in planner.cases_to_run] == ["bbc-terminal"]


def test_phase2_preserves_dataset_order() -> None:
    dataset = _make_dataset(
        [
            _make_case(case_id="z", phase_tags=[PHASE_TAG_REAL_PHASE1]),
            _make_case(case_id="a", phase_tags=[PHASE_TAG_REAL_PHASE1]),
            _make_case(case_id="m", phase_tags=[PHASE_TAG_REAL_PHASE1]),
        ]
    )
    prior_evals = {
        "z": [[_make_dim("answer_success", False)]],
        "a": [[_make_dim("answer_success", False)]],
        "m": [[_make_dim("answer_success", True)]],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results=prior_evals,
    )
    assert [c.id for c in planner.cases_to_run] == ["z", "a"]


# ---------------------------------------------------------------------------
# Phase 3: same selection rule, different prior
# ---------------------------------------------------------------------------


def test_phase3_uses_phase2_prior_results() -> None:
    dataset = _make_dataset(
        [
            _make_case(
                case_id="bbc-still-failing",
                phase_tags=[PHASE_TAG_REAL_PHASE1, PHASE_TAG_PHASE2_CANDIDATE],
            ),
            _make_case(case_id="bbc-fixed", phase_tags=[PHASE_TAG_REAL_PHASE1]),
        ]
    )
    prior_evals = {
        "bbc-still-failing": [[_make_dim("answer_success", False)]],
        "bbc-fixed": [[_make_dim("answer_success", True)]],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=3,
        prior_eval_results=prior_evals,
    )
    assert [c.id for c in planner.cases_to_run] == ["bbc-still-failing"]


# ---------------------------------------------------------------------------
# BudgetStopResult (P0-2)
# ---------------------------------------------------------------------------


def test_record_budget_stop_captures_remaining_work() -> None:
    dataset = _make_dataset([_make_case(case_id="a", phase_tags=[PHASE_TAG_REAL_PHASE1])])
    planner = PhasePlanner(dataset=dataset, phase=1, repetitions=3)
    stop = planner.record_budget_stop(
        executed_requests=10,
        executed_tokens=2000,
        remaining_cases=["a"],
        remaining_run_indices={"a": [2]},
        stop_reason="request_cap_reached",
    )
    assert isinstance(stop, BudgetStopResult)
    assert stop.budget_exhausted is True
    assert stop.executed_requests == 10
    assert stop.executed_tokens == 2000
    assert stop.remaining_cases == ["a"]
    assert stop.remaining_run_indices == {"a": [2]}
    assert stop.stop_reason == "request_cap_reached"
    # The planner exposes the same result via property.
    assert planner.budget_stop_result is stop


def test_budget_stop_result_defaults_stop_reason_when_exhausted() -> None:
    stop = BudgetStopResult(
        budget_exhausted=True,
        executed_requests=5,
        executed_tokens=None,
    )
    assert stop.stop_reason == "budget_exhausted"


def test_budget_stop_result_does_not_default_when_not_exhausted() -> None:
    stop = BudgetStopResult(
        budget_exhausted=False,
        executed_requests=5,
        executed_tokens=None,
    )
    assert stop.stop_reason == ""


# ---------------------------------------------------------------------------
# Repetitions contract (P0-2 core)
# ---------------------------------------------------------------------------


def test_repetitions_is_independent_of_output_retry() -> None:
    """Spec: "independent eval repetitions 与 output retry 是两个概念".

    The planner exposes ``repetitions`` as the number of independent
    runs per case. It does NOT expose any "retry on failure" knob —
    output retry is a runtime concern handled inside the agent, not by
    the planner. The planner always returns the full ``repetitions``
    count regardless of per-run success.
    """
    dataset = _make_dataset(
        [_make_case(case_id="a", phase_tags=[PHASE_TAG_REAL_PHASE1])]
    )
    planner = PhasePlanner(dataset=dataset, phase=1, repetitions=3)
    # The planner does NOT have a "max_retries" or "early_break" knob.
    assert not hasattr(planner, "max_retries")
    assert not hasattr(planner, "early_break_on_success")
    # The harness is expected to run exactly ``repetitions`` times per
    # case, with run_index 0..repetitions-1.
    assert planner.repetitions == 3


def test_phase2_default_repetitions_is_1() -> None:
    """Phase 2/3 default to a single re-run (not 3) — the upgraded model
    is meant to fix the failure, not to measure hallucination rate.
    """
    dataset = _make_dataset(
        [_make_case(case_id="a", phase_tags=[PHASE_TAG_REAL_PHASE1])]
    )
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results={"a": [[_make_dim("answer_success", False)]]},
    )
    assert planner.repetitions == 1


# ---------------------------------------------------------------------------
# P0-1 regression: exact-cap false positive
# ---------------------------------------------------------------------------


def test_exact_cap_10_cases_3_reps_30_max_not_exhausted() -> None:
    """P0-1: 10 cases × 3 reps = 30 (exactly equals max) must NOT trigger
    budget_exhausted.

    Prior bug: the planner checked the hypothetical 11th case AFTER
    appending the 10th, so ``10 * 3 == 30`` falsely returned
    ``budget_exhausted=True`` with ``stop_reason=phase1_independent_run_cap``.
    The fix computes ``allowed = max // reps`` first, then slices — when
    eligible count exactly equals allowed, no BudgetStopResult is recorded.
    """
    cases = [
        _make_case(case_id=f"c{i}", phase_tags=[PHASE_TAG_REAL_PHASE1])
        for i in range(10)
    ]
    dataset = _make_dataset(cases)
    planner = PhasePlanner(
        dataset=dataset,
        phase=1,
        repetitions=3,
        max_independent_runs=30,
    )
    selected = planner.cases_to_run
    assert len(selected) == 10, (
        "exact cap 10×3=30 must select all 10 cases (no truncation)"
    )
    assert [c.id for c in selected] == [f"c{i}" for i in range(10)]
    # P0-1 core assertion: NO BudgetStopResult when cap is exactly met.
    assert planner.budget_stop_result is None, (
        "10×3=30 must NOT set budget_stop_result — the prior bug falsely "
        "marked the 10th case as triggering phase1_independent_run_cap"
    )


def test_over_cap_11_cases_3_reps_30_max_only_11th_remains() -> None:
    """P0-1: 11 cases × 3 reps = 33 > 30 → only the 11th case is remaining.
    """
    cases = [
        _make_case(case_id=f"c{i}", phase_tags=[PHASE_TAG_REAL_PHASE1])
        for i in range(11)
    ]
    dataset = _make_dataset(cases)
    planner = PhasePlanner(
        dataset=dataset,
        phase=1,
        repetitions=3,
        max_independent_runs=30,
    )
    selected = planner.cases_to_run
    assert len(selected) == 10, "only 10 cases fit (10×3=30)"
    assert [c.id for c in selected] == [f"c{i}" for i in range(10)]
    stop = planner.budget_stop_result
    assert stop is not None
    assert stop.budget_exhausted is True
    assert stop.remaining_cases == ["c10"], (
        "only the 11th case (c10) should be in remaining"
    )
    assert stop.remaining_run_indices == {"c10": [0, 1, 2]}
    assert stop.stop_reason == "phase1_independent_run_cap"


def test_offline_only_never_enters_remaining() -> None:
    """P0-1: ``offline_only`` cases are excluded BEFORE the cap is applied,
    so they never enter ``selected`` or ``remaining`` — even when the
    eligible count exceeds the cap.
    """
    cases = [
        _make_case(case_id=f"c{i}", phase_tags=[PHASE_TAG_REAL_PHASE1])
        for i in range(10)
    ]
    # 11th case is offline_only — must NOT appear in remaining.
    cases.append(
        _make_case(
            case_id="offline-1",
            phase_tags=[PHASE_TAG_REAL_PHASE1, PHASE_TAG_OFFLINE_ONLY],
            source_metadata="known_bbc",
        )
    )
    # 12th case is also offline_only.
    cases.append(
        _make_case(
            case_id="offline-2",
            phase_tags=[PHASE_TAG_REAL_PHASE1, PHASE_TAG_OFFLINE_ONLY],
            source_metadata="known_bbc",
        )
    )
    dataset = _make_dataset(cases)
    planner = PhasePlanner(
        dataset=dataset,
        phase=1,
        repetitions=3,
        max_independent_runs=30,
    )
    selected = planner.cases_to_run
    assert len(selected) == 10
    assert "offline-1" not in [c.id for c in selected]
    assert "offline-2" not in [c.id for c in selected]
    stop = planner.budget_stop_result
    # 10 eligible × 3 = 30 = max → no truncation, no BudgetStopResult.
    assert stop is None, (
        "10 eligible × 3 = 30 = max → no remaining; offline_only cases "
        "must NOT inflate the eligible count"
    )


def test_under_cap_no_truncation() -> None:
    """P0-1: under cap → all eligible selected, no BudgetStopResult."""
    cases = [
        _make_case(case_id=f"c{i}", phase_tags=[PHASE_TAG_REAL_PHASE1])
        for i in range(5)
    ]
    dataset = _make_dataset(cases)
    planner = PhasePlanner(
        dataset=dataset,
        phase=1,
        repetitions=3,
        max_independent_runs=30,
    )
    selected = planner.cases_to_run
    assert len(selected) == 5
    assert planner.budget_stop_result is None


def test_cases_to_run_multiple_reads_stable() -> None:
    """P0-1: ``cases_to_run`` is idempotent — multiple reads must return
    the same list with no side effects (no extra BudgetStopResult, no
    mutation of selected/remaining).

    Prior bug risk: if the planner mutated state inside the property,
    repeated reads could accumulate BudgetStopResult or change the
    selected list.
    """
    cases = [
        _make_case(case_id=f"c{i}", phase_tags=[PHASE_TAG_REAL_PHASE1])
        for i in range(10)
    ]
    dataset = _make_dataset(cases)
    planner = PhasePlanner(
        dataset=dataset,
        phase=1,
        repetitions=3,
        max_independent_runs=30,
    )
    first_read = [c.id for c in planner.cases_to_run]
    second_read = [c.id for c in planner.cases_to_run]
    third_read = [c.id for c in planner.cases_to_run]
    assert first_read == second_read == third_read
    assert planner.budget_stop_result is None


# ---------------------------------------------------------------------------
# P0-2 regression: multi-repetition failure aggregation
# ---------------------------------------------------------------------------


def test_fail_then_pass_then_pass_enters_phase2() -> None:
    """P0-2: a case whose first rep fails content checks but subsequent
    reps pass must STILL enter Phase 2.

    Prior bug: ``eval_results[case_id] = evaluate_artifact(...)`` kept
    only the last rep's result, so fail→pass→pass looked like a pass
    and the case was NOT selected for Phase 2 — masking intermittent
    hallucination failures.
    """
    dataset = _make_dataset(
        [_make_case(case_id="bbc-flicker", phase_tags=[PHASE_TAG_REAL_PHASE1])]
    )
    prior_evals = {
        "bbc-flicker": [
            # rep 0: content failure (2025 leaked)
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", False),
                _make_dim("usage_observability", True),
            ],
            # rep 1: clean pass
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
                _make_dim("usage_observability", True),
            ],
            # rep 2: clean pass
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
                _make_dim("usage_observability", True),
            ],
        ],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results=prior_evals,
    )
    assert [c.id for c in planner.cases_to_run] == ["bbc-flicker"], (
        "fail→pass→pass must enter Phase 2 — any failing rep triggers "
        "selection (no more last-rep-wins masking)"
    )


def test_pass_then_pass_then_fail_enters_phase2() -> None:
    """P0-2: a case whose last rep fails but first two pass must enter
    Phase 2. The prior implementation would have caught this (last rep
    wins), but the new implementation must also catch it via the
    ``any_repetition_content_failure`` OR-across-reps logic.
    """
    dataset = _make_dataset(
        [_make_case(case_id="bbc-flicker-end", phase_tags=[PHASE_TAG_REAL_PHASE1])]
    )
    prior_evals = {
        "bbc-flicker-end": [
            # rep 0: clean pass
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
            ],
            # rep 1: clean pass
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
            ],
            # rep 2: content failure (2025 leaked on last rep)
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", False),
            ],
        ],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results=prior_evals,
    )
    assert [c.id for c in planner.cases_to_run] == ["bbc-flicker-end"]


def test_three_passes_does_not_enter_phase2() -> None:
    """P0-2: a case where all 3 reps pass content checks must NOT enter
    Phase 2.
    """
    dataset = _make_dataset(
        [_make_case(case_id="bbc-clean", phase_tags=[PHASE_TAG_REAL_PHASE1])]
    )
    prior_evals = {
        "bbc-clean": [
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
            ],
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
            ],
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
            ],
        ],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results=prior_evals,
    )
    assert planner.cases_to_run == []


def test_shuffled_artifacts_same_selection() -> None:
    """P0-2: aggregation must be order-invariant. The same set of
    per-repetition results, passed in different orders, must produce
    the same Phase 2 selection.

    The implementation sorts artifacts by ``run_index`` before building
    ``PriorEvalResults``, so input order does not affect selection.
    This test verifies that property by shuffling the rep order.
    """
    dataset = _make_dataset(
        [_make_case(case_id="bbc-shuffle", phase_tags=[PHASE_TAG_REAL_PHASE1])]
    )
    # rep 0: fail, rep 1: pass, rep 2: pass
    rep_fail = [
        _make_dim("answer_success", True),
        _make_dim("unsupported_temporal_claims", False),
    ]
    rep_pass = [
        _make_dim("answer_success", True),
        _make_dim("unsupported_temporal_claims", True),
    ]
    # Three different orderings of the same reps.
    orderings = [
        [rep_fail, rep_pass, rep_pass],  # fail first
        [rep_pass, rep_fail, rep_pass],  # fail middle
        [rep_pass, rep_pass, rep_fail],  # fail last
    ]
    for ordering in orderings:
        planner = PhasePlanner(
            dataset=dataset,
            phase=2,
            prior_eval_results={"bbc-shuffle": ordering},
        )
        assert [c.id for c in planner.cases_to_run] == ["bbc-shuffle"], (
            "any rep with content failure must trigger Phase 2 selection "
            "regardless of rep ordering"
        )


def test_terminal_ok_plus_deterministic_hallucination_enters_phase2() -> None:
    """P0-2: a case with ``finalized_status='ok'`` but a deterministic
    hallucination failure (e.g. ``2025`` year token) must enter Phase 2.

    This is the core P0-2/P0-3 scenario: terminal status is OK, but
    content-quality evaluation catches the hallucination. The prior
    implementation only checked terminal status and missed this.
    """
    dataset = _make_dataset(
        [_make_case(case_id="bbc-hallucination", phase_tags=[PHASE_TAG_REAL_PHASE1])]
    )
    prior_evals = {
        "bbc-hallucination": [
            # rep 0: terminal ok (answer_success=True) but temporal fail
            [
                _make_dim("answer_success", True),  # terminal OK
                _make_dim("unsupported_temporal_claims", False),  # 2025 leak
                _make_dim("usage_observability", True),
            ],
        ],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results=prior_evals,
    )
    assert [c.id for c in planner.cases_to_run] == ["bbc-hallucination"], (
        "terminal ok + deterministic hallucination must enter Phase 2 — "
        "content-quality failure is NOT masked by terminal status"
    )


def test_usage_gap_only_across_reps_does_not_enter_phase2() -> None:
    """P0-2: a case where the ONLY failure across ALL reps is
    ``usage_observability`` must NOT enter Phase 2.

    Spec: "默认不要仅因 usage 缺失升级模型" applies per-repetition and
    across repetitions — a usage-only gap never triggers model upgrade.
    """
    dataset = _make_dataset(
        [_make_case(case_id="bbc-usage-only", phase_tags=[PHASE_TAG_REAL_PHASE1])]
    )
    prior_evals = {
        "bbc-usage-only": [
            # rep 0: only usage fails
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
                _make_dim("usage_observability", False),
            ],
            # rep 1: only usage fails
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
                _make_dim("usage_observability", False),
            ],
            # rep 2: only usage fails
            [
                _make_dim("answer_success", True),
                _make_dim("unsupported_temporal_claims", True),
                _make_dim("usage_observability", False),
            ],
        ],
    }
    planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_eval_results=prior_evals,
    )
    assert planner.cases_to_run == []
