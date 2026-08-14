"""Final verdict gate contract tests.

Spec: the accepted final-verdict closure contract.
Requirement: Final Verdict Gate Contract.

Drives :func:`run_reader_record_ask_eval._decide_final_verdict` directly
with minimal :class:`CoverageAuditResult` and :class:`CaseEvalResult`
mocks. Covers all 7 rows of the frozen verdict/gate table plus
identity-mismatch precedence and the single-seam contract.

Frozen 7-row verdict/gate table. Each row's ``(a4, b1)`` pair is
``(quality-gate allowed, streaming-gate allowed)``:

1. identity mismatch                              → blocked_dataset_identity_mismatch (F, F)
2. manifest missing + partial artifacts present   → blocked_incomplete_real_model_run  (F, F)
3. manifest.status="budget_exhausted"             → blocked_incomplete_real_model_run  (F, F)
4. completed manifest + coverage gap (missing/dup)→ blocked_incomplete_real_model_run  (F, F)
5. no manifest + no artifact (not yet run)        → blocked_by_real_model_run          (F, F)
6. completed manifest + full coverage + all pass  → accepted                           (T, T)
7. completed manifest + full coverage + qual fail → rework                             (T, F)

Additional tests:
- identity_mismatch_count precedence over incomplete
- identity_mismatch_count precedence over blocked_by_real_model_run
- deprecated ``_decide_verdict`` removed (single seam)
- ``_decide_final_verdict`` is the only verdict seam (besides the
  ``_decide_normal_verdict`` helper used by it)

No real LLM / provider calls. Mocks use ``SimpleNamespace``.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

from claread_eval.reader_record_ask.evaluators.aggregator import CaseEvalResult
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.run_manifest import CoverageAuditResult

# ---------------------------------------------------------------------------
# Load the runner module (host of _decide_final_verdict).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = (
    _REPO_ROOT / "evals" / "scripts" / "run_reader_record_ask_eval.py"
)


def _load_runner_module():
    """Load the runner script as a module (it's not in a package)."""
    spec = importlib.util.spec_from_file_location(
        "run_reader_record_ask_eval", _RUNNER_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_reader_record_ask_eval"] = module
    spec.loader.exec_module(module)
    return module


_RUNNER = _load_runner_module()

# ---------------------------------------------------------------------------
# Helpers — minimal CoverageAuditResult / CaseEvalResult factories.
# ---------------------------------------------------------------------------


def _coverage_audit(
    *,
    manifest_present: bool = True,
    manifest_status: str | None = "completed",
    planned_count: int = 1,
    completed_count: int = 1,
    missing_count: int = 0,
    duplicate_count: int = 0,
    unexpected_count: int = 0,
    identity_mismatch_count: int = 0,
    evaluable_artifact_count: int = 1,
    dataset_identity: tuple[str, str, str] | None = ("ds", "v1", "sha"),
    manifest_state: str | None = None,
    manifest_run_id_matches: bool | None = None,
) -> CoverageAuditResult:
    """Build a minimal CoverageAuditResult for verdict-table tests.

    ``manifest_state`` defaults to ``"absent"`` when
    ``manifest_present=False`` and ``"valid"`` when
    ``manifest_present=True``. Callers can override to ``"corrupt"``
    to test the manifest three-state contract.
    """
    if manifest_state is None:
        manifest_state = "valid" if manifest_present else "absent"
    if manifest_run_id_matches is None and manifest_present:
        # Default to True for valid manifests (backward compat).
        manifest_run_id_matches = True
    return CoverageAuditResult(
        manifest_present=manifest_present,
        manifest_status=manifest_status,
        planned_count=planned_count,
        completed_count=completed_count,
        missing_count=missing_count,
        duplicate_count=duplicate_count,
        unexpected_count=unexpected_count,
        identity_mismatch_count=identity_mismatch_count,
        evaluable_artifact_count=evaluable_artifact_count,
        dataset_identity=dataset_identity,
        missing_run_indices={},
        duplicate_run_indices={},
        unexpected_run_indices={},
        manifest_state=manifest_state,
        manifest_run_id_matches=manifest_run_id_matches,
    )


def _passing_case_result(case_id: str = "case-a") -> CaseEvalResult:
    return CaseEvalResult(
        case_id=case_id,
        run_id="phase1-test",
        run_index=0,
        model_short_name="test-model",
        thinking_enabled=False,
        dimensions=[
            EvalDimensionResult(
                dimension="answer_success",
                passed=True,
                severity="none",
            ),
            EvalDimensionResult(
                dimension="context_support",
                passed=True,
                severity="none",
            ),
        ],
        latency_seconds=1.0,
        total_tokens=10,
        total_requests=1,
    )


def _failing_case_result(case_id: str = "case-a") -> CaseEvalResult:
    return CaseEvalResult(
        case_id=case_id,
        run_id="phase1-test",
        run_index=0,
        model_short_name="test-model",
        thinking_enabled=False,
        dimensions=[
            EvalDimensionResult(
                dimension="answer_success",
                passed=False,
                severity="high",
                details="hallucinated year token",
            ),
        ],
        latency_seconds=1.0,
        total_tokens=10,
        total_requests=1,
    )


# ---------------------------------------------------------------------------
# Row 1: identity mismatch → blocked_dataset_identity_mismatch
# ---------------------------------------------------------------------------


def test_verdict_identity_mismatch_blocks_everything() -> None:
    """identity_mismatched_count=1, all case_results pass →
    blocked_dataset_identity_mismatch, False, False.

    Identity mismatch is the strongest blocker — even if the
    evaluator reported all passes, the aggregate cannot trust any
    artifact's binding to the current dataset.
    """
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="completed",
        identity_mismatch_count=1,
        evaluable_artifact_count=0,
    )
    case_results = [_passing_case_result()]
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=case_results,
        coverage_audit=audit,
        identity_mismatched_count=1,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=2,
    )
    assert verdict == "blocked_dataset_identity_mismatch"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Row 5: no manifest + no artifact → blocked_by_real_model_run, False, False
# (This is the bug-fix row — the quality gate was incorrectly allowed.)
# ---------------------------------------------------------------------------


def test_verdict_no_manifest_no_artifact_blocked_by_real_model_run() -> None:
    """manifest_present=False, total_artifacts=0 →
    blocked_by_real_model_run, False, False.

    Critical: the quality gate MUST be disabled per the frozen 7-row contract.
    The previous implementation incorrectly returned True here — this
    test is the regression guard for that bug fix.
    """
    audit = _coverage_audit(
        manifest_present=False,
        manifest_status=None,
        planned_count=0,
        completed_count=0,
        evaluable_artifact_count=0,
        dataset_identity=None,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=True,
        has_budget_exhausted=False,
        total_artifacts_loaded=0,
    )
    assert verdict == "blocked_by_real_model_run"
    assert allow_a4 is False, (
        "BUG FIX: blocked_by_real_model_run MUST return allow_correctness_followup=False "
        "per the frozen 7-row contract. Previous implementation incorrectly "
        "returned True."
    )
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Row 2: manifest missing + partial artifacts → blocked_incomplete_real_model_run
# ---------------------------------------------------------------------------


def test_verdict_partial_artifacts_no_manifest_blocked_incomplete() -> None:
    """manifest_present=False, total_artifacts=3 →
    blocked_incomplete_real_model_run, False, False.

    Process-like interruption: phase process killed before writing a
    manifest, leaving partial artifacts on disk. The run is incomplete
    and MUST NOT enter accepted/rework.
    """
    audit = _coverage_audit(
        manifest_present=False,
        manifest_status=None,
        planned_count=0,
        completed_count=0,
        evaluable_artifact_count=3,
        dataset_identity=None,
    )
    # Pass case_results to prove the verdict does NOT enter accepted/rework
    # even when quality looks good — the coverage gap blocks first.
    case_results = [_passing_case_result()]
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=case_results,
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=3,
    )
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Row 3: budget_exhausted → blocked_incomplete_real_model_run
# ---------------------------------------------------------------------------


def test_verdict_budget_exhausted_blocked_incomplete() -> None:
    """manifest_status='budget_exhausted', total_artifacts=2 →
    blocked_incomplete_real_model_run, False, False.

    Budget stop is NOT a completed run. The partial case_results MUST
    NOT enter accepted/rework — this is the run-id binding bug fix.
    """
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="budget_exhausted",
        planned_count=4,
        completed_count=2,
        missing_count=2,
        evaluable_artifact_count=2,
    )
    case_results = [_passing_case_result(), _passing_case_result()]
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=case_results,
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=True,
        total_artifacts_loaded=2,
    )
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Row 4a: completed manifest + missing artifact → blocked_incomplete
# ---------------------------------------------------------------------------


def test_verdict_completed_manifest_coverage_gap_blocked_incomplete() -> None:
    """manifest_status='completed', missing_count=1 →
    blocked_incomplete_real_model_run, False, False."""
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="completed",
        planned_count=2,
        completed_count=2,
        missing_count=1,
        evaluable_artifact_count=1,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=1,
    )
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Row 4b: completed manifest + duplicate → blocked_incomplete
# ---------------------------------------------------------------------------


def test_verdict_completed_manifest_duplicate_blocked_incomplete() -> None:
    """manifest_status='completed', duplicate_count=1 →
    blocked_incomplete_real_model_run, False, False."""
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="completed",
        planned_count=1,
        completed_count=1,
        duplicate_count=1,
        evaluable_artifact_count=1,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=2,
    )
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Row 4c: completed manifest + unexpected artifact → blocked_incomplete
# ---------------------------------------------------------------------------


def test_verdict_completed_manifest_unexpected_blocked_incomplete() -> None:
    """manifest_status='completed', unexpected_count=1 →
    blocked_incomplete_real_model_run, False, False."""
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="completed",
        planned_count=1,
        completed_count=1,
        unexpected_count=1,
        evaluable_artifact_count=1,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=2,
    )
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Row 6: completed + full coverage + all pass → accepted, True, True
# ---------------------------------------------------------------------------


def test_verdict_completed_full_coverage_all_pass_accepted() -> None:
    """completed, missing=0, duplicate=0, unexpected=0, identity_mismatch=0,
    all case_results pass → accepted, True, True."""
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="completed",
        planned_count=1,
        completed_count=1,
        missing_count=0,
        duplicate_count=0,
        unexpected_count=0,
        identity_mismatch_count=0,
        evaluable_artifact_count=1,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=1,
    )
    assert verdict == "accepted"
    assert allow_a4 is True
    assert allow_b1 is True


# ---------------------------------------------------------------------------
# Row 7: completed + full coverage + quality failure → rework, True, False
# ---------------------------------------------------------------------------


def test_verdict_completed_full_coverage_quality_failure_rework() -> None:
    """Same as accepted row but case_results has a high-severity failure →
    rework, True, False."""
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="completed",
        planned_count=1,
        completed_count=1,
        missing_count=0,
        duplicate_count=0,
        unexpected_count=0,
        identity_mismatch_count=0,
        evaluable_artifact_count=1,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_failing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=1,
    )
    assert verdict == "rework"
    assert allow_a4 is True
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Precedence: identity mismatch > incomplete (budget + partial)
# ---------------------------------------------------------------------------


def test_verdict_identity_mismatch_precedence_over_incomplete() -> None:
    """identity_mismatch=1 + budget_exhausted + partial artifacts →
    blocked_dataset_identity_mismatch (identity wins over incomplete).

    The operator needs to see the drift reason first — fixing the
    drift is a prerequisite before any rerun makes sense.
    """
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="budget_exhausted",
        planned_count=4,
        completed_count=2,
        missing_count=2,
        identity_mismatch_count=1,
        evaluable_artifact_count=1,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=1,  # ← wins over budget_exhausted
        real_model_blocked=False,
        has_budget_exhausted=True,
        total_artifacts_loaded=2,
    )
    assert verdict == "blocked_dataset_identity_mismatch"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Precedence: identity mismatch > blocked_by_real_model_run
# ---------------------------------------------------------------------------


def test_verdict_identity_mismatch_precedence_over_blocked_by_real_model_run() -> None:
    """identity_mismatch=1 + no manifest + no artifact →
    blocked_dataset_identity_mismatch (identity wins over
    blocked_by_real_model_run).

    This case is unusual (no manifest, no artifact, but
    identity_mismatched_count > 0) but the precedence must hold
    structurally — if any mismatch signal is present, the operator
    sees the drift reason, not the "open the gate" message.
    """
    audit = _coverage_audit(
        manifest_present=False,
        manifest_status=None,
        planned_count=0,
        completed_count=0,
        identity_mismatch_count=0,  # coverage_audit sees no mismatch (no manifest)
        evaluable_artifact_count=0,
        dataset_identity=None,
    )
    # The aggregate computes identity_mismatched_count as the max of
    # coverage_audit.identity_mismatch_count and the count from
    # find_identity_mismatched_artifacts. Here we directly drive the
    # function with identity_mismatched_count=1 to test the precedence.
    verdict, _, _ = _RUNNER._decide_final_verdict(
        case_results=[],
        coverage_audit=audit,
        identity_mismatched_count=1,  # ← wins over blocked_by_real_model_run
        real_model_blocked=True,
        has_budget_exhausted=False,
        total_artifacts_loaded=0,
    )
    assert verdict == "blocked_dataset_identity_mismatch"


# ---------------------------------------------------------------------------
# Single seam: deprecated _decide_verdict removed
# ---------------------------------------------------------------------------


def test_verdict_no_deprecated_decide_verdict_function() -> None:
    """The deprecated ``_decide_verdict`` function MUST NOT exist.

    Spec Requirement: Final Verdict Gate Contract / Scenario: 单一
    verdict seam. The deprecated ``_decide_verdict`` (which did not
    honor identity/manifest precedence) MUST be removed. All callers
    go through ``_decide_final_verdict``.
    """
    assert not hasattr(_RUNNER, "_decide_verdict"), (
        "Deprecated _decide_verdict MUST be removed — it bypassed the "
        "full verdict contract (no identity/manifest/coverage signal)."
    )

    # Defensive: also grep the source file for any lingering definition
    # (e.g. as a comment or string). The function name MUST NOT appear
    # as a top-level ``def _decide_verdict``.
    source = _RUNNER_PATH.read_text(encoding="utf-8")
    # Allow the name to appear in docstrings/comments (we don't ban the
    # word itself), but ban a real ``def _decide_verdict(`` definition.
    assert "def _decide_verdict(" not in source, (
        "Source still contains a ``def _decide_verdict(`` definition — "
        "the deprecated seam MUST be deleted entirely."
    )


# ---------------------------------------------------------------------------
# Single seam: _decide_final_verdict is the only verdict function
# ---------------------------------------------------------------------------


def test_verdict_single_seam_only_decide_final_verdict() -> None:
    """``_decide_final_verdict`` is the SINGLE verdict seam.

    The runner may keep ``_decide_normal_verdict`` as a helper called
    ONLY from ``_decide_final_verdict`` (it computes accepted/rework
    from case_results). No other ``_decide_*verdict*`` function may
    exist at module level.
    """
    # _decide_final_verdict MUST exist.
    assert hasattr(_RUNNER, "_decide_final_verdict"), (
        "_decide_final_verdict MUST exist as the single verdict seam."
    )
    # _decide_normal_verdict is allowed as a helper.
    assert hasattr(_RUNNER, "_decide_normal_verdict"), (
        "_decide_normal_verdict helper MUST exist (used by "
        "_decide_final_verdict for accepted/rework)."
    )

    # Find every module-level callable matching ``_decide_*verdict*``.
    verdict_fns = [
        name
        for name, obj in vars(_RUNNER).items()
        if name.startswith("_decide_")
        and "verdict" in name
        and inspect.isfunction(obj)
    ]
    assert sorted(verdict_fns) == ["_decide_final_verdict", "_decide_normal_verdict"], (
        f"Expected exactly [_decide_final_verdict, _decide_normal_verdict] "
        f"as verdict-related functions; got {sorted(verdict_fns)!r}."
    )

    # _decide_normal_verdict MUST be called only from _decide_final_verdict
    # (not from aggregate or any other module-level function). We verify
    # by inspecting the source of _decide_final_verdict and aggregate.
    final_verdict_src = inspect.getsource(_RUNNER._decide_final_verdict)
    aggregate_src = inspect.getsource(_RUNNER.aggregate)
    assert "_decide_normal_verdict" in final_verdict_src, (
        "_decide_final_verdict MUST call _decide_normal_verdict for the "
        "accepted/rework path."
    )
    assert "_decide_normal_verdict" not in aggregate_src, (
        "aggregate() MUST NOT call _decide_normal_verdict directly — it "
        "goes through _decide_final_verdict (the single seam)."
    )


# ---------------------------------------------------------------------------
# Sanity: SimpleNamespace smoke test (sanity-check the mock factory).
# ---------------------------------------------------------------------------


def test_verdict_mock_factories_produce_expected_types() -> None:
    """Sanity check: mock factories produce objects with the fields
    _decide_final_verdict / _decide_normal_verdict actually read."""
    audit = _coverage_audit()
    # CoverageAuditResult is a frozen dataclass — verify field access.
    assert audit.manifest_present is True
    assert audit.manifest_status == "completed"
    assert audit.missing_count == 0

    # CaseEvalResult carries dimensions with .passed / .severity.
    passing = _passing_case_result()
    failing = _failing_case_result()
    assert all(d.passed for d in passing.dimensions)
    assert any(not d.passed for d in failing.dimensions)
    assert any(d.severity == "high" for d in failing.dimensions)

    # SimpleNamespace smoke (used by the coverage_audit tests).
    stub = SimpleNamespace(case_id="c1", run_index=0)
    assert stub.case_id == "c1"
    assert stub.run_index == 0


# ===========================================================================
# Adversarial tests — foreign manifest, corrupt manifest, and
# three-state verdict routing. ALL tests drive the production
# ``_decide_final_verdict`` seam (no copied verdict branches). The tests
# assert the 9-row verdict/gate table including the foreign-manifest
# and corrupt-manifest rows.
# ===========================================================================


# ---------------------------------------------------------------------------
# Adversarial 15: foreign manifest (run_id mismatch) + matching artifacts
#                 → blocked_incomplete_real_model_run
# ---------------------------------------------------------------------------


def test_verdict_foreign_manifest_with_matching_artifacts_blocked() -> None:
    """A foreign manifest (valid content but wrong run_id) MUST
    NOT be stitched together with the current run's artifacts, even
    when dataset identity, case ids, and run indices all match.

    Verdict: ``blocked_incomplete_real_model_run`` (NOT
    ``blocked_dataset_identity_mismatch`` — the dataset identity itself
    matches; only the run_id is wrong).

    both optional gates disabled per the frozen
    9-row contract (precedence 4).

    Drives the production ``_decide_final_verdict`` seam directly:
    manifest_state="valid" + manifest_run_id_matches=False triggers
    precedence 4. No copied verdict branch.
    """
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="completed",
        planned_count=1,
        completed_count=1,
        missing_count=0,
        duplicate_count=0,
        unexpected_count=0,
        identity_mismatch_count=0,  # dataset identity MATCHES
        evaluable_artifact_count=1,
        manifest_state="valid",
        manifest_run_id_matches=False,  # ← foreign manifest
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=0,  # no dataset identity mismatch
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=1,
    )
    assert verdict == "blocked_incomplete_real_model_run", (
        "Foreign manifest MUST yield blocked_incomplete_real_model_run, "
        "NOT blocked_dataset_identity_mismatch (dataset identity matches) "
        "and NOT accepted (run_id mismatch means the manifest does not "
        f"audit THIS run). Got {verdict!r}."
    )
    assert allow_a4 is False
    assert allow_b1 is False


def test_verdict_foreign_manifest_zero_artifacts_still_blocked_incomplete() -> None:
    """Boundary: foreign manifest + zero artifacts → still
    ``blocked_incomplete_real_model_run`` (NOT
    ``blocked_by_real_model_run``). The presence of a foreign manifest
    file indicates a previous run existed — the operator needs to see
    "ran but unauditable", not "never ran".
    """
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="completed",
        planned_count=0,
        completed_count=0,
        evaluable_artifact_count=0,
        dataset_identity=None,
        manifest_state="valid",
        manifest_run_id_matches=False,  # ← foreign manifest
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=True,  # no artifacts
        has_budget_exhausted=False,
        total_artifacts_loaded=0,
    )
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Adversarial 16: corrupt manifest + no artifacts → blocked_incomplete
# ---------------------------------------------------------------------------


def test_verdict_corrupt_manifest_no_artifacts_blocked_incomplete() -> None:
    """A corrupt manifest (file exists but unparseable / invalid) +
    NO artifacts → ``blocked_incomplete_real_model_run``.

    This is the manifest-state fix: previously corrupt was folded into absent,
    causing "corrupt + no artifacts" to be misclassified as
    ``blocked_by_real_model_run`` (the "never ran" verdict). A corrupt
    manifest indicates the run started but its audit trail is broken
    — strictly worse than "never ran".

    Drives the production ``_decide_final_verdict`` seam:
    manifest_state="corrupt" triggers precedence 2 (before the absent
    check at precedence 3).
    """
    audit = _coverage_audit(
        manifest_present=False,  # corrupt manifest is NOT used for audit
        manifest_status=None,
        planned_count=0,
        completed_count=0,
        evaluable_artifact_count=0,
        dataset_identity=None,
        manifest_state="corrupt",  # ← corrupt manifest state
        manifest_run_id_matches=None,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=True,  # no artifacts
        has_budget_exhausted=False,
        total_artifacts_loaded=0,
    )
    assert verdict == "blocked_incomplete_real_model_run", (
        "Corrupt manifest + no artifacts MUST yield "
        "blocked_incomplete_real_model_run (NOT blocked_by_real_model_run). "
        "A corrupt manifest indicates the run started but its audit trail "
        "is broken — strictly worse than 'never ran'."
    )
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Adversarial 17: corrupt manifest + partial artifacts → blocked_incomplete
# ---------------------------------------------------------------------------


def test_verdict_corrupt_manifest_partial_artifacts_blocked_incomplete() -> None:
    """Corrupt manifest + partial artifacts on disk →
    ``blocked_incomplete_real_model_run``.

    The corrupt manifest cannot audit the artifacts — the run is
    unauditable regardless of how many artifacts exist.

    Drives the production ``_decide_final_verdict`` seam: precedence 2
    (corrupt) fires before precedence 3 (absent + partial) or
    precedence 6 (coverage gap). The case_results are passed to prove
    the verdict does NOT enter accepted/rework even when quality looks
    good.
    """
    audit = _coverage_audit(
        manifest_present=False,  # corrupt manifest is NOT used for audit
        manifest_status=None,
        planned_count=0,
        completed_count=0,
        evaluable_artifact_count=3,  # partial artifacts exist
        dataset_identity=None,
        manifest_state="corrupt",  # ← corrupt manifest state
        manifest_run_id_matches=None,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result()],  # quality "looks good"
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=3,
    )
    assert verdict == "blocked_incomplete_real_model_run", (
        "Corrupt manifest + partial artifacts MUST yield "
        "blocked_incomplete_real_model_run — the corrupt audit trail "
        "blocks the verdict BEFORE quality is considered."
    )
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Adversarial 18: absent manifest + no artifacts → blocked_by_real_model_run
# (regression — already exists as test_verdict_no_manifest_no_artifact_*
# but we add an explicit manifest_state="absent" variant for the absent-manifest
# three-state contract.)
# ---------------------------------------------------------------------------


def test_verdict_absent_manifest_no_artifacts_remains_blocked_by_real_model_run() -> (
    None
):
    """Regression: absent manifest + no artifacts MUST stay
    ``blocked_by_real_model_run`` (NOT be reclassified as
    ``blocked_incomplete_real_model_run`` by accident).

    This is the "never ran" path — distinct from corrupt. The manifest-state fix
    adds the corrupt state but MUST NOT change the absent path.
    """
    audit = _coverage_audit(
        manifest_present=False,
        manifest_status=None,
        planned_count=0,
        completed_count=0,
        evaluable_artifact_count=0,
        dataset_identity=None,
        manifest_state="absent",  # ← explicit absent
        manifest_run_id_matches=None,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=True,
        has_budget_exhausted=False,
        total_artifacts_loaded=0,
    )
    assert verdict == "blocked_by_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Adversarial 19: identity mismatch precedence over corrupt manifest
# ---------------------------------------------------------------------------


def test_verdict_identity_mismatch_precedence_over_corrupt_manifest() -> None:
    """Identity-mismatch precedence wins over
    corrupt manifest (precedence 2). When BOTH signals are present,
    the operator sees the dataset drift reason, not the corrupt-manifest
    reason — fixing the drift is the prerequisite.
    """
    audit = _coverage_audit(
        manifest_present=False,
        manifest_status=None,
        planned_count=0,
        completed_count=0,
        identity_mismatch_count=1,
        evaluable_artifact_count=0,
        dataset_identity=None,
        manifest_state="corrupt",  # ← corrupt manifest
        manifest_run_id_matches=None,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[],
        coverage_audit=audit,
        identity_mismatched_count=1,  # ← wins over corrupt
        real_model_blocked=True,
        has_budget_exhausted=False,
        total_artifacts_loaded=1,
    )
    assert verdict == "blocked_dataset_identity_mismatch"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Adversarial 20: completed manifest + full coverage + all pass → accepted
# (regression — already exists as test_verdict_completed_full_coverage_*,
# but we add an explicit manifest_state="valid" + manifest_run_id_matches=True
# variant for the run-id binding contract.)
# ---------------------------------------------------------------------------


def test_verdict_completed_full_coverage_valid_match_accepted_no_regression() -> (
    None
):
    """Regression: completed manifest + full coverage + all pass +
    manifest_state="valid" + manifest_run_id_matches=True → accepted.

    The new run-id binding and manifest three-state checks MUST NOT
    break the normal accepted path. The ``coverage_ok`` gate in
    production ``aggregate()`` requires both
    ``manifest_state == "valid"`` AND ``manifest_run_id_matches is True``
    — this test confirms the verdict function reaches precedence 8/9
    (normal path) under those conditions.
    """
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="completed",
        planned_count=1,
        completed_count=1,
        missing_count=0,
        duplicate_count=0,
        unexpected_count=0,
        identity_mismatch_count=0,
        evaluable_artifact_count=1,
        manifest_state="valid",  # ← valid manifest state
        manifest_run_id_matches=True,  # ← matching run binding
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=1,
    )
    assert verdict == "accepted"
    assert allow_a4 is True
    assert allow_b1 is True


# ---------------------------------------------------------------------------
# Adversarial 21: budget_exhausted partial run → blocked_incomplete
# (regression — already exists as test_verdict_budget_exhausted_*,
# but we add an explicit manifest_state="valid" + manifest_run_id_matches=True
# variant for the run-id binding contract.)
# ---------------------------------------------------------------------------


def test_verdict_budget_exhausted_partial_run_no_regression() -> None:
    """Regression: budget_exhausted partial run with
    manifest_state="valid" + manifest_run_id_matches=True →
    ``blocked_incomplete_real_model_run``. The new binding and state checks
    MUST NOT change the budget_exhausted path.
    """
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="budget_exhausted",
        planned_count=4,
        completed_count=2,
        missing_count=2,
        evaluable_artifact_count=2,
        manifest_state="valid",
        manifest_run_id_matches=True,
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result(), _passing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=True,  # ← budget signal
        total_artifacts_loaded=2,
    )
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Adversarial 22: precedence ordering — corrupt > absent+partial
# ---------------------------------------------------------------------------


def test_verdict_corrupt_beats_absent_partial_precedence() -> None:
    """Corrupt-manifest precedence beats
    absent + partial artifacts (precedence 3). When the manifest is
    corrupt, the verdict is ``blocked_incomplete_real_model_run``
    regardless of artifact count — the corrupt audit trail blocks
    before the absent+partial check fires.
    """
    # manifest_state="corrupt" + total_artifacts_loaded=3.
    # Precedence 2 (corrupt) MUST fire before precedence 3 (absent +
    # partial), so the verdict is blocked_incomplete_real_model_run
    # via precedence 2, not precedence 3.
    audit = _coverage_audit(
        manifest_present=False,
        manifest_status=None,
        planned_count=0,
        completed_count=0,
        evaluable_artifact_count=3,
        dataset_identity=None,
        manifest_state="corrupt",
        manifest_run_id_matches=None,
    )
    verdict, _, _ = _RUNNER._decide_final_verdict(
        case_results=[],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=3,
    )
    assert verdict == "blocked_incomplete_real_model_run"


# ---------------------------------------------------------------------------
# Adversarial 23: precedence ordering — foreign manifest > budget_exhausted
# ---------------------------------------------------------------------------


def test_verdict_foreign_manifest_beats_budget_exhausted_precedence() -> None:
    """Foreign-manifest precedence beats
    budget_exhausted (precedence 5). When the manifest is foreign AND
    status="budget_exhausted", the operator sees
    ``blocked_incomplete_real_model_run`` via the foreign-manifest
    path — the budget signal is irrelevant because the manifest does
    not audit THIS run.
    """
    audit = _coverage_audit(
        manifest_present=True,
        manifest_status="budget_exhausted",
        planned_count=4,
        completed_count=2,
        missing_count=2,
        evaluable_artifact_count=2,
        manifest_state="valid",
        manifest_run_id_matches=False,  # ← foreign manifest
    )
    verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
        case_results=[_passing_case_result(), _passing_case_result()],
        coverage_audit=audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=True,  # ← budget signal present
        total_artifacts_loaded=2,
    )
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False
