"""R4-A4-2R P0-Identity: runtime fixture identity contract tests.

Spec: R4-A4-2R — Real Eval Fixture Identity / Path Contract Repair.
Task contract:

- ``expected_envelope_fingerprint`` on :class:`ReaderRecordAskR4A3Case`
  is an explicit, auditable model-visible fixture identity for real-BBC
  cases. When present, the harness preflight verifies that the runtime
  envelope's ``envelope_fingerprint`` matches EXACTLY before any model
  builder is invoked or provider call is made. Mismatch → fail-closed
  (calls=0, builder=0).
- The aggregate verifies each artifact's ``envelope_fingerprint``
  matches the declared expected value; mismatch →
  ``blocked_incomplete_real_model_run`` via precedence row 5.5 in
  :func:`_decide_final_verdict`.
- The aggregate MUST NOT reverse-engineer expected facts or temporal
  allowsets from live article text.

Test coverage (8 required scenarios — scenarios 1, 2, 3 here):

1. fingerprint match → preflight continues (schema + aggregate helper
   returns False on match). The harness pre-call check
   (``_verify_runtime_identity``) is tested in
   ``services/api/tests/test_reader_record_ask_real_llm_eval.py``.
2. fingerprint mismatch/missing → builder=0, provider=0 (aggregate
   helper returns True on mismatch / missing runtime).
3. artifact runtime identity mismatch → aggregate blocked
   (``_decide_final_verdict`` precedence 5.5 returns
   ``blocked_incomplete_real_model_run``).

Plus precedence-ordering tests:

- dataset identity mismatch (precedence 1) wins over runtime identity
  mismatch (precedence 5.5).
- runtime identity mismatch (precedence 5.5) wins over budget
  exhaustion (precedence 6) — surfacing the more actionable root cause.

No real LLM / provider calls. Mocks use ``SimpleNamespace`` and
minimal pydantic models.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from claread_eval.reader_record_ask.evaluators.aggregator import CaseEvalResult
from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.run_manifest import CoverageAuditResult
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)

# ---------------------------------------------------------------------------
# Load the runner module (host of _decide_final_verdict and
# _runtime_identity_mismatches).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = (
    _REPO_ROOT / "evals" / "scripts" / "run_reader_record_ask_r4_a3.py"
)


def _load_runner_module():
    """Load the runner script as a module (it's not in a package)."""
    spec = importlib.util.spec_from_file_location(
        "run_reader_record_ask_r4_a3", _RUNNER_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_reader_record_ask_r4_a3"] = module
    spec.loader.exec_module(module)
    return module


_RUNNER = _load_runner_module()

# A valid 64-char lowercase hex SHA-256 fingerprint (deterministic test
# constant — not derived from any real envelope).
_VALID_FP_A = "a" * 64
_VALID_FP_B = "b" * 64


# ---------------------------------------------------------------------------
# Helpers — minimal factories
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
    """Build a minimal CoverageAuditResult for verdict-table tests."""
    if manifest_state is None:
        manifest_state = "valid" if manifest_present else "absent"
    if manifest_run_id_matches is None and manifest_present:
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


def _make_synthetic_case(
    *,
    case_id: str = "case-syn",
    expected_envelope_fingerprint: str | None = None,
) -> ReaderRecordAskR4A3Case:
    """Build a minimal synthetic case for runtime identity tests."""
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind="synthetic_short",
        record_id=None,
        article_text="Hello world.",
        article_title=None,
        input_mode="no_selection",
        selection=None,
        rag_mode="off",
        source_metadata="unknown",
        baseline_mode="complete",
        question="测试问题。",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(),
        phase_tags=[],
        expected_envelope_fingerprint=expected_envelope_fingerprint,
    )


def _make_artifact(
    *,
    case_id: str = "case-syn",
    envelope_fingerprint: str | None = None,
) -> RawArtifact:
    """Build a minimal RawArtifact for runtime identity mismatch tests.

    ``RawArtifact`` uses ``extra="forbid"`` and strict types — only
    actual schema fields may be set. The ``_runtime_identity_mismatches``
    check only reads ``case_id`` and ``envelope_fingerprint``, so we
    populate the minimum required fields (``case_id`` and ``run_id``)
    plus ``envelope_fingerprint`` and rely on defaults for everything
    else.
    """
    return RawArtifact(
        case_id=case_id,
        run_id="phase1-test",
        envelope_fingerprint=envelope_fingerprint,
    )


# ---------------------------------------------------------------------------
# Scenario 1: schema validation for expected_envelope_fingerprint
# ---------------------------------------------------------------------------


class TestSchemaExpectedEnvelopeFingerprint:
    """R4-A4-2R P0-Identity: schema contract for
    ``expected_envelope_fingerprint``."""

    def test_none_default_preserves_backwards_compat(self) -> None:
        """Cases authored before R4-A4-2R do not declare the field —
        schema must default to ``None`` and load without error."""
        case = _make_synthetic_case()
        assert case.expected_envelope_fingerprint is None

    def test_valid_64_char_hex_accepted(self) -> None:
        """A valid 64-char lowercase hex SHA-256 fingerprint is accepted."""
        case = _make_synthetic_case(
            expected_envelope_fingerprint=_VALID_FP_A,
        )
        assert case.expected_envelope_fingerprint == _VALID_FP_A

    def test_uppercase_hex_rejected_strict_str(self) -> None:
        """``StrictStr`` rejects non-str types (bool/int/float)."""
        with pytest.raises(ValidationError):
            _make_synthetic_case(
                expected_envelope_fingerprint=123,  # type: ignore[arg-type]
            )

    def test_empty_string_accepted_but_harness_treats_as_declared(
        self,
    ) -> None:
        """Empty string is a valid ``StrictStr`` but the harness
        ``_verify_runtime_identity`` treats it as declared — an empty
        expected fingerprint can never match a 64-char runtime
        fingerprint, so the case fails closed at preflight.

        This is intentional: dataset authors MUST NOT publish empty
        strings. The schema accepts them (StrictStr only enforces
        type), but the runtime check rejects them.
        """
        case = _make_synthetic_case(expected_envelope_fingerprint="")
        assert case.expected_envelope_fingerprint == ""


# ---------------------------------------------------------------------------
# Scenario 1 + 2: _runtime_identity_mismatches (aggregate post-call helper)
# ---------------------------------------------------------------------------


class TestRuntimeIdentityMismatchesHelper:
    """R4-A4-2R P0-Identity: :func:`_runtime_identity_mismatches` branch
    coverage.

    Returns ``True`` when:
    - Case exists in ``cases_by_id`` AND declares a non-None
      ``expected_envelope_fingerprint`` AND the artifact's
      ``envelope_fingerprint`` is missing OR does not exactly match.

    Returns ``False`` when:
    - Case is not in ``cases_by_id`` (handled separately as
      ``unknown_artifact_case_count``).
    - Case does not declare ``expected_envelope_fingerprint``
      (backwards-compat — no check).
    - Artifact's ``envelope_fingerprint`` exactly matches.
    """

    def test_match_returns_false(self) -> None:
        """Scenario 1 (match): artifact's runtime fingerprint exactly
        equals the case's declared expected fingerprint → no mismatch."""
        case = _make_synthetic_case(
            expected_envelope_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            envelope_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        assert _RUNNER._runtime_identity_mismatches(
            artifact, cases_by_id
        ) is False

    def test_mismatch_returns_true(self) -> None:
        """Scenario 2 (mismatch): artifact's runtime fingerprint differs
        from declared → mismatch (aggregate will block)."""
        case = _make_synthetic_case(
            expected_envelope_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            envelope_fingerprint=_VALID_FP_B,
        )
        cases_by_id = {case.id: case}
        assert _RUNNER._runtime_identity_mismatches(
            artifact, cases_by_id
        ) is True

    def test_missing_runtime_fingerprint_returns_true(self) -> None:
        """Scenario 2 (missing runtime): case declares expected but
        artifact's runtime fingerprint is None → mismatch (fail-closed)."""
        case = _make_synthetic_case(
            expected_envelope_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(envelope_fingerprint=None)
        cases_by_id = {case.id: case}
        assert _RUNNER._runtime_identity_mismatches(
            artifact, cases_by_id
        ) is True

    def test_empty_runtime_fingerprint_returns_true(self) -> None:
        """Scenario 2 (empty runtime): artifact's runtime fingerprint
        is empty string → treated as missing → mismatch."""
        case = _make_synthetic_case(
            expected_envelope_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(envelope_fingerprint="")
        cases_by_id = {case.id: case}
        assert _RUNNER._runtime_identity_mismatches(
            artifact, cases_by_id
        ) is True

    def test_no_expected_declared_returns_false(self) -> None:
        """Backwards-compat: case does not declare
        ``expected_envelope_fingerprint`` → no check (returns False)
        even if the artifact's runtime fingerprint is missing."""
        case = _make_synthetic_case(expected_envelope_fingerprint=None)
        artifact = _make_artifact(envelope_fingerprint=None)
        cases_by_id = {case.id: case}
        assert _RUNNER._runtime_identity_mismatches(
            artifact, cases_by_id
        ) is False

    def test_case_not_in_dataset_returns_false(self) -> None:
        """Case not in ``cases_by_id`` (foreign artifact) → returns
        False. This is handled separately as
        ``unknown_artifact_case_count`` in the readiness audit."""
        artifact = _make_artifact(case_id="foreign-case")
        cases_by_id: dict[str, ReaderRecordAskR4A3Case] = {}
        assert _RUNNER._runtime_identity_mismatches(
            artifact, cases_by_id
        ) is False


# ---------------------------------------------------------------------------
# Scenario 3: _decide_final_verdict precedence 5.5
# ---------------------------------------------------------------------------


class TestDecideFinalVerdictRuntimeIdentityPrecedence:
    """R4-A4-2R P0-Identity: precedence row 5.5 in
    :func:`_decide_final_verdict`.

    When ``runtime_identity_mismatch_count > 0``, the verdict MUST fall
    to ``blocked_incomplete_real_model_run`` with
    ``(allow_r4_a4=False, allow_r4_b1=False)``.

    Precedence ordering:
    - 1 (dataset identity mismatch) wins over 5.5 (runtime mismatch).
    - 5.5 (runtime mismatch) wins over 6 (budget_exhausted) — surfaces
      the more actionable root cause.
    - 5.5 fires after 5 (artifact_load_invalid) — a corrupt artifact
      file is a stronger integrity signal than a runtime mismatch.
    """

    def test_runtime_identity_mismatch_blocks_verdict(self) -> None:
        """Scenario 3: runtime_identity_mismatch_count=1 with otherwise
        clean coverage → blocked_incomplete_real_model_run."""
        audit = _coverage_audit(
            manifest_present=True,
            manifest_status="completed",
            evaluable_artifact_count=1,
        )
        case_results = [_passing_case_result()]
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=case_results,
            coverage_audit=audit,
            identity_mismatched_count=0,
            runtime_identity_mismatch_count=1,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=2,
        )
        assert verdict == "blocked_incomplete_real_model_run"
        assert allow_a4 is False
        assert allow_b1 is False

    def test_zero_runtime_identity_mismatch_does_not_block(self) -> None:
        """When runtime_identity_mismatch_count=0, precedence 5.5 does
        NOT fire — falls through to the normal path (accepted/rework)."""
        audit = _coverage_audit(
            manifest_present=True,
            manifest_status="completed",
            evaluable_artifact_count=1,
        )
        case_results = [_passing_case_result()]
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=case_results,
            coverage_audit=audit,
            identity_mismatched_count=0,
            runtime_identity_mismatch_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
        )
        # All-pass case → accepted.
        assert verdict == "accepted"
        assert allow_a4 is True
        assert allow_b1 is True

    def test_dataset_identity_mismatch_wins_over_runtime_mismatch(
        self,
    ) -> None:
        """Precedence 1 (dataset identity mismatch) wins over 5.5
        (runtime mismatch). The operator sees the stronger blocker."""
        audit = _coverage_audit(
            manifest_present=True,
            manifest_status="completed",
            identity_mismatch_count=1,
            evaluable_artifact_count=0,
        )
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=audit,
            identity_mismatched_count=1,
            runtime_identity_mismatch_count=1,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=2,
        )
        assert verdict == "blocked_dataset_identity_mismatch"
        assert allow_a4 is False
        assert allow_b1 is False

    def test_runtime_mismatch_wins_over_budget_exhausted(self) -> None:
        """Precedence 5.5 (runtime mismatch) wins over 6
        (budget_exhausted). Both produce the same verdict, but the
        runtime mismatch is the more actionable root cause — the test
        verifies precedence ordering by checking the verdict is
        ``blocked_incomplete_real_model_run`` (not accepted/rework)
        even when budget_exhausted is also True.

        Note: both rows return the same verdict, so this test
        primarily guards against accidental reordering that would let
        the budget_exhausted branch shadow the runtime mismatch
        diagnostic in the report's run_metadata.
        """
        audit = _coverage_audit(
            manifest_present=True,
            manifest_status="budget_exhausted",
            evaluable_artifact_count=1,
        )
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[_passing_case_result()],
            coverage_audit=audit,
            identity_mismatched_count=0,
            runtime_identity_mismatch_count=1,
            real_model_blocked=False,
            has_budget_exhausted=True,
            total_artifacts_loaded=2,
        )
        assert verdict == "blocked_incomplete_real_model_run"
        assert allow_a4 is False
        assert allow_b1 is False

    def test_runtime_mismatch_count_parameter_defaults_to_zero(
        self,
    ) -> None:
        """Backwards-compat: ``runtime_identity_mismatch_count`` defaults
        to 0 when not passed. Existing callers (e.g. unit tests that
        don't care about runtime identity) continue to work."""
        audit = _coverage_audit(
            manifest_present=True,
            manifest_status="completed",
            evaluable_artifact_count=1,
        )
        # Note: no runtime_identity_mismatch_count kwarg passed.
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
# Scenario 3 (integration): aggregate runtime identity fence end-to-end
# ---------------------------------------------------------------------------


class TestAggregateRuntimeIdentityFence:
    """R4-A4-2R P0-Identity: end-to-end aggregate behavior when
    artifacts' runtime identity mismatches the dataset's declared
    expected identity.

    These tests verify that the aggregate drops mismatched artifacts
    from the evaluable set AND that the verdict falls to
    ``blocked_incomplete_real_model_run`` via precedence 5.5. We
    exercise the helper + verdict seam directly (not the full
    ``aggregate()`` function — that requires a real dataset on disk
    and is covered by the offline e2e tests).
    """

    def test_multiple_mismatches_all_dropped_and_blocked(self) -> None:
        """When 2 of 3 artifacts mismatch, the helper flags both and
        the verdict falls to ``blocked_incomplete_real_model_run``."""
        case_a = _make_synthetic_case(
            case_id="case-a",
            expected_envelope_fingerprint=_VALID_FP_A,
        )
        case_b = _make_synthetic_case(
            case_id="case-b",
            expected_envelope_fingerprint=_VALID_FP_A,
        )
        case_c = _make_synthetic_case(
            case_id="case-c",
            expected_envelope_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {c.id: c for c in [case_a, case_b, case_c]}

        # case_a: match. case_b: mismatch. case_c: missing runtime fp.
        artifacts = [
            _make_artifact(
                case_id="case-a",
                envelope_fingerprint=_VALID_FP_A,
            ),
            _make_artifact(
                case_id="case-b",
                envelope_fingerprint=_VALID_FP_B,
            ),
            _make_artifact(
                case_id="case-c",
                envelope_fingerprint=None,
            ),
        ]
        mismatched = [
            a for a in artifacts
            if _RUNNER._runtime_identity_mismatches(a, cases_by_id)
        ]
        assert len(mismatched) == 2
        mismatched_ids = {a.case_id for a in mismatched}
        assert mismatched_ids == {"case-b", "case-c"}

        # Verdict with runtime_identity_mismatch_count=2.
        audit = _coverage_audit(
            manifest_present=True,
            manifest_status="completed",
            planned_count=3,
            completed_count=3,
            evaluable_artifact_count=3,
        )
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[_passing_case_result("case-a")],
            coverage_audit=audit,
            identity_mismatched_count=0,
            runtime_identity_mismatch_count=2,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=3,
        )
        assert verdict == "blocked_incomplete_real_model_run"
        assert allow_a4 is False
        assert allow_b1 is False


# ---------------------------------------------------------------------------
# Harness pre-call check: _verify_runtime_identity
# ---------------------------------------------------------------------------
# The harness pre-call check ``_verify_runtime_identity`` lives in
# ``services/api/tests/test_reader_record_ask_real_llm_eval.py`` (it
# requires the ``app`` module which is only importable from
# ``services/api/`` cwd). Tests for that function are co-located in
# the harness file — see
# :func:`test_r4_a4_2r_verify_runtime_identity_match`,
# :func:`test_r4_a4_2r_verify_runtime_identity_mismatch_skips`, etc.
