"""Aggregate verdict precedence tests.

Spec: ``docs/tmp/reader-orchestration/review/...
TMP-reader-record-ask-r4-a3-eval-2026-07-17.md`` (aggregate-verdict rework) +
the accepted aggregate-verdict closure specification
(7-row verdict/gate table).

Background: the prior aggregate() implementation set
``verdict = "blocked_dataset_identity_mismatch"`` and then OVERWROTE it
with ``"blocked_by_real_model_run"`` whenever ``real_artifacts`` was
empty after filtering mismatched artifacts. That overwrite hid the real
blocker reason: an operator looking at ``blocked_by_real_model_run``
would try to open the real-LLM gate, when the actual problem was
dataset drift between phases.

The rework establishes a SINGLE source of truth —
:func:`run_reader_record_ask_eval._decide_final_verdict` — implementing
the 7-row verdict/gate table (frozen contract)::

    1. dataset/manifest/artifact identity mismatch
       → blocked_dataset_identity_mismatch, both optional gates disabled
    2. manifest missing but partial artifacts present
       → blocked_incomplete_real_model_run, False, False
    3. manifest.status="budget_exhausted"
       → blocked_incomplete_real_model_run, False, False
    4. completed manifest + coverage gap (missing/duplicate/unexpected)
       → blocked_incomplete_real_model_run, False, False
    5. no manifest + no artifact (not yet real-run)
       → blocked_by_real_model_run, False, False (BUG FIX: was True/False)
    6. completed manifest + full coverage + all pass
       → accepted, True, True
    7. completed manifest + full coverage + quality failure
       → rework, True, False

These tests drive BOTH the verdict production seam directly AND the
production ``aggregate()`` function end-to-end (writing artifacts to a
tmp runs dir, then reading the generated report). The end-to-end path
proves the precedence is honored through the full integration, not just
in the unit-level helper.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from claread_eval.reader_record_ask.evaluators.aggregator import CaseEvalResult
from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.run_manifest import (
    MANIFEST_SCHEMA_VERSION,
    CoverageAuditResult,
    ReaderRecordAskRunManifest,
    write_manifest_atomic,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskExpected,
)
from claread_eval.reader_record_ask.session import RunSessionLayout

# ---------------------------------------------------------------------------
# Load the runner module (host of _decide_final_verdict + aggregate).
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
# Helpers — minimal CaseEvalResult / RawArtifact factories.
# ---------------------------------------------------------------------------


def _make_passing_case_result(
    *,
    case_id: str = "case-a",
    run_id: str = "phase1-test",
) -> CaseEvalResult:
    """Build a CaseEvalResult where every non-usage dimension passes.

    Used for the "normal verdict" precedence branch: when no identity
    mismatch is present and case_results is non-empty, the verdict MUST
    be ``accepted`` (all passed, no high-severity failures).
    """
    return CaseEvalResult(
        case_id=case_id,
        run_id=run_id,
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


def _make_failing_case_result(
    *,
    case_id: str = "case-a",
    run_id: str = "phase1-test",
) -> CaseEvalResult:
    """Build a CaseEvalResult with a high-severity failure.

    The normal-path verdict for this input is ``rework`` (quality gate
    conditionally allowed, streaming gate deferred).
    """
    return CaseEvalResult(
        case_id=case_id,
        run_id=run_id,
        run_index=0,
        model_short_name="test-model",
        thinking_enabled=False,
        dimensions=[
            EvalDimensionResult(
                dimension="answer_success",
                passed=False,
                severity="high",
                details="hallucinated 2025 year token",
            ),
        ],
        latency_seconds=1.0,
        total_tokens=10,
        total_requests=1,
    )


def _make_artifact(
    *,
    case_id: str = "case-a",
    run_id: str = "phase1-test",
    dataset_id: str | None = "test-dataset",
    dataset_schema_version: str | None = "test-schema-v1",
    # Strict contract: default must be a valid 64-lowercase-hex SHA
    # so the helper produces a schema-valid RawArtifact by default.
    # Callers that want to test identity mismatch pass a different
    # valid 64-hex SHA (e.g. ``"0" * 64``) or ``None``.
    dataset_content_sha256: str | None = "a" * 64,
    budget_exhausted: bool = False,
) -> RawArtifact:
    """Build a minimal RawArtifact with the dataset identity fields."""
    return RawArtifact(
        case_id=case_id,
        run_id=run_id,
        dataset_id=dataset_id,
        dataset_schema_version=dataset_schema_version,
        dataset_content_sha256=dataset_content_sha256,
        budget_exhausted=budget_exhausted,
    )


def _make_case(
    *,
    case_id: str = "case-a",
    article_text: str = "Hello world.",
) -> ReaderRecordAskCase:
    # ``phase_tags`` is intentionally empty. These tests
    # exercise the aggregate VERDICT precedence (identity mismatch,
    # budget exhaustion, coverage gap, normal path) — NOT the runtime
    # fixture identity contract. Tagging the case as ``real_phase1``
    # would trigger the strict runtime-fixture requirement that
    # ``expected_runtime_fixture_fingerprint`` be declared, which is
    # orthogonal to what these tests verify.
    return ReaderRecordAskCase(
        id=case_id,
        source_kind="synthetic_short",
        article_text=article_text,
        article_title=None,
        input_mode="manual",
        selection=None,
        rag_mode="off",
        source_metadata="unknown",
        baseline_mode="complete",
        question="What is this about?",
        question_category="main_idea",
        expected=ReaderRecordAskExpected(),
        tags=[],
        phase_tags=[],
    )


def _write_dataset_dir(
    dataset_dir: Path,
    *,
    cases: list[ReaderRecordAskCase],
    dataset_id: str = "test-dataset",
    schema_version: str = "test-schema-v1",
) -> Path:
    """Write a minimal dataset dir matching the loader's contract."""
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "cases").mkdir(exist_ok=True)
    yaml_payload = {
        "id": dataset_id,
        "schema_version": schema_version,
        "description": "synthetic test dataset",
        "case_globs": ["cases/*.json"],
        "tags": [],
    }
    (dataset_dir / "dataset.yaml").write_text(
        yaml.safe_dump(yaml_payload, sort_keys=False),
        encoding="utf-8",
    )
    for case in cases:
        (dataset_dir / "cases" / f"{case.id}.json").write_text(
            case.model_dump_json(indent=2),
            encoding="utf-8",
        )
    return dataset_dir


def _write_artifact(
    runs_dir: Path,
    *,
    run_id: str,
    artifact: RawArtifact,
) -> Path:
    """Write a RawArtifact to ``<runs_dir>/<run_id>/artifacts/<filename>.json``."""
    artifact_dir = runs_dir / run_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    fname = (
        f"{artifact.case_id}__{artifact.model_short_name or 'none'}__"
        f"{'thinking' if artifact.thinking_enabled else 'nothinking'}__"
        f"{artifact.run_index:03d}.json"
    )
    path = artifact_dir / fname
    # Use model_dump_json to round-trip identity fields correctly.
    path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    return path


def _make_coverage_audit(
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
    """Build a minimal CoverageAuditResult for direct verdict-table tests.

    The ``*_run_indices`` dicts are kept empty — the verdict function
    only reads the count fields, not the indices.

    ``manifest_state`` defaults to ``"absent"`` when
    ``manifest_present=False`` and ``"valid"`` when
    ``manifest_present=True``. Callers can override to ``"corrupt"``
    to test the manifest's three-state contract.

    ``manifest_run_id_matches`` defaults to ``True`` when the manifest
    is present+valid (foreign-manifest tests override to ``False``).
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


def _write_completed_manifest(
    runs_dir: Path,
    *,
    run_id: str,
    planned: dict[str, list[int]],
    dataset_id: str,
    dataset_schema_version: str,
    dataset_content_sha256: str,
    phase: int = 1,
) -> Path:
    """Write a status="completed" manifest at RunSessionLayout.manifest_path.

    The manifest's planned == completed (full coverage) and remaining is
    empty. Used by end-to-end tests that need the coverage audit to pass
    so the evaluator runs and the verdict follows the normal path.
    """
    layout = RunSessionLayout(runs_root=runs_dir, run_id=run_id)
    manifest = ReaderRecordAskRunManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id=run_id,
        phase=phase,
        dataset_id=dataset_id,
        dataset_schema_version=dataset_schema_version,
        dataset_content_sha256=dataset_content_sha256,
        status="completed",
        planned_run_indices=planned,
        completed_run_indices={k: list(v) for k, v in planned.items()},
        remaining_run_indices={},
        executed_requests=0,
        executed_tokens=0,
        stop_reason=None,
    )
    write_manifest_atomic(manifest, layout.manifest_path)
    return layout.manifest_path


# ---------------------------------------------------------------------------
# Direct tests against the single verdict production seam.
# ---------------------------------------------------------------------------


class TestDecideFinalVerdictPrecedence:
    """Drive ``_decide_final_verdict`` directly.

    This is the SINGLE source of truth for the verdict precedence. The
    tests below cover every branch of the precedence ladder::

        identity_mismatched_count > 0
          → ("blocked_dataset_identity_mismatch", False, False)
        real_model_blocked or no case_results
          → ("blocked_by_real_model_run", True, False)
        normal path
          → _decide_normal_verdict(case_results)

    The previous implementation first set ``blocked_dataset_identity_mismatch``
    then OVERWROTE it with ``blocked_by_real_model_run`` when
    ``real_artifacts`` became empty after filtering mismatched ones.
    The precedence here makes that overwrite structurally impossible.
    """

    def test_partial_mismatch_wins_over_blocked_by_real_model_run(self) -> None:
        """Partial mismatch (some valid + some mismatched) → verdict
        MUST be ``blocked_dataset_identity_mismatch`` even when
        ``real_model_blocked`` is True.

        This is the regression test for the original bug: when some
        artifacts mismatch and the rest are dropped, ``real_artifacts``
        becomes empty and ``real_model_blocked`` becomes True. The
        verdict MUST stay ``blocked_dataset_identity_mismatch`` — the
        operator needs to see the drift, not a misleading "open the
        real-LLM gate" message.
        """
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[],  # empty after filtering mismatched
            coverage_audit=_make_coverage_audit(
                manifest_present=False,
                manifest_status=None,
                planned_count=0,
                completed_count=0,
                evaluable_artifact_count=0,
                dataset_identity=None,
            ),
            real_model_blocked=True,  # all artifacts were mismatched
            has_budget_exhausted=False,
            identity_mismatched_count=2,  # ← the real blocker
            total_artifacts_loaded=2,
        )
        assert verdict == "blocked_dataset_identity_mismatch"
        assert allow_a4 is False
        assert allow_b1 is False

    def test_all_mismatch_does_not_downgrade_to_blocked_by_real_model_run(self) -> None:
        """When ALL artifacts mismatch, ``real_artifacts`` is empty
        and ``real_model_blocked`` is True. The verdict MUST stay
        ``blocked_dataset_identity_mismatch`` — NOT be downgraded to
        ``blocked_by_real_model_run``.

        This is the exact scenario the previous implementation got wrong:
        the overwrite path was reached because every artifact mismatched.
        """
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=_make_coverage_audit(
                manifest_present=False,
                manifest_status=None,
                planned_count=0,
                completed_count=0,
                evaluable_artifact_count=0,
                dataset_identity=None,
            ),
            real_model_blocked=True,
            has_budget_exhausted=False,
            identity_mismatched_count=5,  # all artifacts mismatched
            total_artifacts_loaded=5,
        )
        assert verdict == "blocked_dataset_identity_mismatch", (
            "All-mismatch MUST NOT be downgraded to blocked_by_real_model_run "
            "— the operator needs to see the drift reason."
        )
        assert allow_a4 is False
        assert allow_b1 is False

    def test_all_artifacts_missing_identity_treated_as_mismatch(self) -> None:
        """Artifacts with missing identity fields from older runs
        artifacts) are flagged as mismatched by
        ``find_identity_mismatched_artifacts``. The verdict MUST be
        ``blocked_dataset_identity_mismatch``.

        The harness MUST NOT silently re-evaluate old artifacts against
        the current dataset — their identity is unauditable.
        """
        # When all artifacts are old (missing identity), the aggregate
        # path flags them as mismatched (identity_mismatched_count > 0)
        # and drops them from real_artifacts. The verdict sees both
        # signals: mismatched_count > 0 AND real_model_blocked=True.
        # Precedence 1 wins.
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=_make_coverage_audit(
                manifest_present=False,
                manifest_status=None,
                planned_count=0,
                completed_count=0,
                evaluable_artifact_count=0,
                dataset_identity=None,
            ),
            real_model_blocked=True,
            has_budget_exhausted=False,
            identity_mismatched_count=3,  # all 3 artifacts missing identity
            total_artifacts_loaded=3,
        )
        assert verdict == "blocked_dataset_identity_mismatch"

    def test_no_artifacts_no_mismatch_yields_blocked_by_real_model_run(self) -> None:
        """When no artifacts exist AND no identity mismatch is
        present, the verdict is ``blocked_by_real_model_run``.

        This is the "gate never opened" / "first run before any real
        artifacts exist" path. Per the frozen 7-row contract,
        the quality gate remains disabled (BUG FIX: previous implementation
        incorrectly returned ``True`` — the deterministic
        harness/evaluator/dataset being accepted does NOT unlock the quality gate
        when the real-model validation is entirely absent).
        """
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=_make_coverage_audit(
                manifest_present=False,
                manifest_status=None,
                planned_count=0,
                completed_count=0,
                evaluable_artifact_count=0,
                dataset_identity=None,
            ),
            real_model_blocked=True,
            has_budget_exhausted=False,
            identity_mismatched_count=0,  # ← no mismatch
            total_artifacts_loaded=0,
        )
        assert verdict == "blocked_by_real_model_run"
        assert allow_a4 is False, (
            "BUG FIX: blocked_by_real_model_run MUST return allow_correctness_followup=False "
            "per the frozen 7-row contract. Previous implementation incorrectly "
            "returned True."
        )
        assert allow_b1 is False

    def test_all_valid_artifacts_yields_normal_accepted_verdict(self) -> None:
        """When case_results is non-empty and no identity mismatch
        is present, the verdict follows the normal path
        (``_decide_normal_verdict``). For all-passing results, the
        verdict is ``accepted`` with both optional gates allowed.

        This is the non-regression test: the precedence fix MUST NOT
        break the normal accepted path. The coverage_audit signal here
        reflects a completed manifest with full coverage (row 6 of the
        7-row table).
        """
        case_results = [_make_passing_case_result()]
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=case_results,
            coverage_audit=_make_coverage_audit(
                manifest_present=True,
                manifest_status="completed",
                planned_count=1,
                completed_count=1,
                missing_count=0,
                duplicate_count=0,
                unexpected_count=0,
                identity_mismatch_count=0,
                evaluable_artifact_count=1,
            ),
            real_model_blocked=False,
            has_budget_exhausted=False,
            identity_mismatched_count=0,
            total_artifacts_loaded=1,
        )
        assert verdict == "accepted"
        assert allow_a4 is True
        assert allow_b1 is True

    def test_all_valid_artifacts_with_failure_yields_rework(self) -> None:
        """A normal-path high-severity failure yields ``rework``
        (quality gate conditionally allowed, streaming gate deferred).

        Rework is the normal verdict when real-model validation
        surfaced actionable failures. The streaming gate is False to avoid streaming
        + correctness churn while rework is in flight. The coverage
        audit reflects row 7 of the 7-row table (completed + full
        coverage + quality failure).
        """
        case_results = [_make_failing_case_result()]
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=case_results,
            coverage_audit=_make_coverage_audit(
                manifest_present=True,
                manifest_status="completed",
                planned_count=1,
                completed_count=1,
                missing_count=0,
                duplicate_count=0,
                unexpected_count=0,
                identity_mismatch_count=0,
                evaluable_artifact_count=1,
            ),
            real_model_blocked=False,
            has_budget_exhausted=False,
            identity_mismatched_count=0,
            total_artifacts_loaded=1,
        )
        assert verdict == "rework"
        assert allow_a4 is True
        assert allow_b1 is False

    def test_identity_mismatch_wins_over_budget_exhausted(self) -> None:
        """Identity mismatch wins over budget exhaustion
        (budget exhausted / incomplete run).

        Even if the run was budget-exhausted AND had identity drift,
        the verdict MUST reflect the identity mismatch — fixing the
        drift is a prerequisite before any rerun makes sense.
        """
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[],  # budget exhausted before any artifact produced
            coverage_audit=_make_coverage_audit(
                manifest_present=True,
                manifest_status="budget_exhausted",
                planned_count=1,
                completed_count=0,
                evaluable_artifact_count=0,
            ),
            real_model_blocked=True,
            has_budget_exhausted=True,  # ← budget signal present
            identity_mismatched_count=1,  # ← but identity mismatch wins
            total_artifacts_loaded=1,
        )
        assert verdict == "blocked_dataset_identity_mismatch"

    def test_identity_mismatch_wins_over_normal_accepted(self) -> None:
        """Identity mismatch wins over a normal accepted verdict
        (normal accepted).

        Even if case_results is non-empty and would otherwise produce
        ``accepted``, a single mismatched artifact forces the
        ``blocked_dataset_identity_mismatch`` verdict. The aggregate
        cannot trust any artifact's binding to the current dataset.
        """
        # In production, mismatched artifacts are dropped from
        # case_results before _decide_final_verdict is called. But the
        # precedence function is defensive: even if case_results is
        # non-empty, identity_mismatched_count > 0 wins.
        case_results = [_make_passing_case_result()]
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=case_results,
            coverage_audit=_make_coverage_audit(
                manifest_present=True,
                manifest_status="completed",
                identity_mismatch_count=1,
                evaluable_artifact_count=1,
            ),
            real_model_blocked=False,
            has_budget_exhausted=False,
            identity_mismatched_count=1,  # ← wins over normal accepted
            total_artifacts_loaded=2,
        )
        assert verdict == "blocked_dataset_identity_mismatch"
        assert allow_a4 is False
        assert allow_b1 is False


# ---------------------------------------------------------------------------
# End-to-end tests against the production aggregate() function.
# ---------------------------------------------------------------------------


class TestAggregateEndToEndVerdictPrecedence:
    """Drive the production ``aggregate()`` function end-to-end.

    These tests prove the precedence is honored through the FULL
    integration path:

        load dataset snapshot → load artifacts from disk →
        find mismatched → filter → _decide_final_verdict →
        generate_eval_report → write report

    The verdict is extracted from the generated report's title block
    (``> verdict: **<verdict>**``).
    """

    def _setup_dataset_and_runs(
        self,
        tmp_path: Path,
        *,
        artifacts: list[RawArtifact],
        run_id: str = "phase1-test",
    ) -> tuple[Path, Path, Path]:
        """Set up a minimal dataset dir + runs dir + report path.

        Returns ``(dataset_dir, runs_dir, report_output)``.
        """
        # Dataset: one synthetic case, identity computed by the loader.
        cases = [_make_case(case_id="case-a")]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)

        # Runs dir: write each artifact under runs/<run_id>/artifacts/.
        runs_dir = tmp_path / "runs"
        for artifact in artifacts:
            _write_artifact(
                runs_dir,
                run_id=artifact.run_id,
                artifact=artifact,
            )

        report_output = tmp_path / "report.md"
        return dataset_dir, runs_dir, report_output

    def _read_verdict(self, report_path: Path) -> str:
        """Extract just the verdict string from the report."""
        text = report_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("> verdict:"):
                start = line.find("**")
                end = line.find("**", start + 2)
                assert start != -1 and end != -1, (
                    f"could not parse verdict from line: {line!r}"
                )
                return line[start + 2 : end]
        pytest.fail(f"report missing verdict line; first 500 chars: {text[:500]!r}")

    def test_aggregate_partial_mismatch_yields_blocked_dataset_identity_mismatch(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: some artifacts match the dataset identity, some
        don't. The aggregate MUST report
        ``blocked_dataset_identity_mismatch``.

        The previous implementation would have produced
        ``blocked_by_real_model_run`` here (mismatched artifacts
        filtered → real_artifacts might still be non-empty but the
        verdict was overwritten by the real_model_blocked branch).
        """
        # Build artifacts with identity fields matching the dataset's
        # computed identity. We need to load the dataset first to get
        # the actual content_sha256.
        from claread_eval.reader_record_ask.loader import (
            load_reader_record_ask_dataset_with_snapshot,
        )

        cases = [_make_case(case_id="case-a"), _make_case(case_id="case-b")]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)
        snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
        identity = snapshot.identity

        # case-a: matches identity.
        # case-b: mismatched sha (will be flagged by find_identity_mismatched).
        artifacts = [
            _make_artifact(
                case_id="case-a",
                run_id="phase1-test",
                dataset_id=identity.dataset_id,
                dataset_schema_version=identity.schema_version,
                dataset_content_sha256=identity.content_sha256,
            ),
            _make_artifact(
                case_id="case-b",
                run_id="phase1-test",
                dataset_id=identity.dataset_id,
                dataset_schema_version=identity.schema_version,
                dataset_content_sha256="0" * 64,  # mismatched
            ),
        ]
        runs_dir = tmp_path / "runs"
        for artifact in artifacts:
            _write_artifact(runs_dir, run_id="phase1-test", artifact=artifact)
        report_output = tmp_path / "report.md"

        rc = _RUNNER.aggregate(
            run_id="phase1-test",
            runs_dir=runs_dir,
            dataset_dir=dataset_dir,
            report_output=report_output,
        )
        assert rc == 0
        verdict = self._read_verdict(report_output)
        assert verdict == "blocked_dataset_identity_mismatch", (
            "Partial mismatch MUST yield blocked_dataset_identity_mismatch "
            f"(got {verdict!r}). The previous implementation would have "
            "downgraded this to blocked_by_real_model_run."
        )

    def test_aggregate_all_mismatch_yields_blocked_dataset_identity_mismatch(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: ALL artifacts mismatch the current dataset
        identity. The verdict MUST stay
        ``blocked_dataset_identity_mismatch`` — NOT be downgraded to
        ``blocked_by_real_model_run``.

        This is the exact regression scenario: all-mismatch makes
        ``real_artifacts`` empty after filtering, which previously
        triggered the overwrite to ``blocked_by_real_model_run``.
        """
        cases = [_make_case(case_id="case-a")]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)

        # All artifacts carry a mismatched identity.
        artifacts = [
            _make_artifact(
                case_id="case-a",
                run_id="phase1-test",
                dataset_id="test-dataset",
                dataset_schema_version="test-schema-v1",
                dataset_content_sha256="0" * 64,  # mismatched
            ),
        ]
        runs_dir = tmp_path / "runs"
        for artifact in artifacts:
            _write_artifact(runs_dir, run_id="phase1-test", artifact=artifact)
        report_output = tmp_path / "report.md"

        rc = _RUNNER.aggregate(
            run_id="phase1-test",
            runs_dir=runs_dir,
            dataset_dir=dataset_dir,
            report_output=report_output,
        )
        assert rc == 0
        verdict = self._read_verdict(report_output)
        assert verdict == "blocked_dataset_identity_mismatch", (
            "All-mismatch MUST NOT be downgraded to blocked_by_real_model_run "
            f"(got {verdict!r})"
        )

    def test_aggregate_all_artifacts_missing_identity_yields_blocked_dataset_identity_mismatch(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: all artifacts are from older runs (missing identity
        fields). The aggregate MUST treat them as mismatched and report
        ``blocked_dataset_identity_mismatch``.

        Old artifacts without fingerprints are NOT silently
        re-evaluated — their identity is unauditable.
        """
        cases = [_make_case(case_id="case-a")]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)

        # All three identity fields None (default).
        artifacts = [
            _make_artifact(
                case_id="case-a",
                run_id="phase1-test",
                dataset_id=None,
                dataset_schema_version=None,
                dataset_content_sha256=None,
            ),
        ]
        runs_dir = tmp_path / "runs"
        for artifact in artifacts:
            _write_artifact(runs_dir, run_id="phase1-test", artifact=artifact)
        report_output = tmp_path / "report.md"

        rc = _RUNNER.aggregate(
            run_id="phase1-test",
            runs_dir=runs_dir,
            dataset_dir=dataset_dir,
            report_output=report_output,
        )
        assert rc == 0
        verdict = self._read_verdict(report_output)
        assert verdict == "blocked_dataset_identity_mismatch", (
            "Old artifacts missing identity MUST yield "
            f"blocked_dataset_identity_mismatch (got {verdict!r})"
        )

    def test_aggregate_no_artifacts_no_mismatch_yields_blocked_by_real_model_run(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: no artifacts on disk AND no identity mismatch →
        ``blocked_by_real_model_run``.

        This is the "gate never opened" / "first run before any real
        artifacts exist" path. The quality gate is conditionally allowed.
        """
        cases = [_make_case(case_id="case-a")]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)

        # Empty runs dir — no artifacts.
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir(parents=True)
        report_output = tmp_path / "report.md"

        rc = _RUNNER.aggregate(
            run_id="phase1-test",
            runs_dir=runs_dir,
            dataset_dir=dataset_dir,
            report_output=report_output,
        )
        assert rc == 0
        verdict = self._read_verdict(report_output)
        assert verdict == "blocked_by_real_model_run", (
            "No artifacts + no mismatch MUST yield blocked_by_real_model_run "
            f"(got {verdict!r})"
        )

    def test_aggregate_all_valid_artifacts_yields_normal_verdict(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: all artifacts match the current dataset identity
        AND a completed manifest with full coverage exists → normal
        verdict path (no blocking).

        Non-regression test: the precedence fix MUST NOT break the
        normal path. With all-passing dimensions the verdict is
        ``accepted``; with failures it's ``rework``. We only assert
        the verdict is NOT one of the blocked variants.

        Per the 7-row contract, the normal path requires:
        - manifest present + status="completed"
        - full coverage (no missing/duplicate/unexpected/identity mismatch)
        - identity_mismatched_count == 0
        Only then does _decide_final_verdict reach row 6/7
        (_decide_normal_verdict). Without a manifest, the verdict
        falls to row 2 (blocked_incomplete_real_model_run) — so this
        test MUST write a completed manifest matching the dataset
        identity to reach the normal path.

        Final gate closure: "all-valid" under the
        new contract means the artifact carries the explicit captured
        lifecycle (``model_context_instrumentation_version=v1``,
        ``model_context_capture_status=captured``). Legacy artifacts
        (no lifecycle fields) are correctly blocked by
        ``legacy_artifact_count`` in :class:`AggregateReadinessAudit`
        — see ``test_aggregate_legacy_artifact_yields_blocked_incomplete``
        for that contract. This test builds a captured artifact so the
        normal-path precedence row is reachable.
        """
        from claread_eval.reader_record_ask.loader import (
            load_reader_record_ask_dataset_with_snapshot,
        )

        cases = [_make_case(case_id="case-a")]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)
        snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
        identity = snapshot.identity

        # Artifact carries the matching identity AND the captured
        # lifecycle fields (so it is NOT classified as legacy).
        artifacts = [
            _make_artifact(
                case_id="case-a",
                run_id="phase1-test",
                dataset_id=identity.dataset_id,
                dataset_schema_version=identity.schema_version,
                dataset_content_sha256=identity.content_sha256,
            ).model_copy(
                update={
                    "model_context_instrumentation_version": (
                        "reader_record_ask_model_context_v1"
                    ),
                    "model_context_capture_status": "captured",
                    "model_context_fingerprint": "a" * 64,
                    "model_context_handle_ids": ["evh_" + "0" * 32],
                }
            ),
        ]
        runs_dir = tmp_path / "runs"
        for artifact in artifacts:
            _write_artifact(runs_dir, run_id="phase1-test", artifact=artifact)

        # Write a completed manifest with full coverage matching the
        # artifact's identity. Without this, coverage_audit reports
        # manifest_present=False → row 2 (blocked_incomplete_real_model_run)
        # fires before the normal path is reached.
        _write_completed_manifest(
            runs_dir,
            run_id="phase1-test",
            planned={"case-a": [0]},
            dataset_id=identity.dataset_id,
            dataset_schema_version=identity.schema_version,
            dataset_content_sha256=identity.content_sha256,
        )
        report_output = tmp_path / "report.md"

        rc = _RUNNER.aggregate(
            run_id="phase1-test",
            runs_dir=runs_dir,
            dataset_dir=dataset_dir,
            report_output=report_output,
        )
        assert rc == 0
        verdict = self._read_verdict(report_output)
        # With one artifact and (likely) failing dimensions (we didn't
        # populate final_text / evidence), the normal-path verdict is
        # either ``rework`` or ``accepted``. Either is acceptable for
        # this test — the contract is "NOT blocked_*".
        assert verdict in ("accepted", "rework"), (
            "All-valid artifacts MUST yield a normal verdict "
            f"(accepted/rework), not a blocked variant (got {verdict!r})"
        )
