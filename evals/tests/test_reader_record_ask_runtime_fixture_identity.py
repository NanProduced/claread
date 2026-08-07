"""Runtime fixture identity tests (reader_record_ask eval).

Covers fingerprint determinism/sensitivity, the three-layer
dataset/manifest/artifact identity check, required-facts precheck, and
budget semantics. No real LLM / provider calls; all data is mocked or
built from minimal pydantic models.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.runtime_fixture import (
    RUNTIME_FIXTURE_FINGERPRINT_PATTERN,
    compute_runtime_fixture_fingerprint,
    is_valid_runtime_fixture_fingerprint,
    precheck_required_facts_support,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)

# ---------------------------------------------------------------------------
# Load the runner module (host of _decide_final_verdict,
# _runtime_fixture_identity_mismatches, _compute_budget_semantics).
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

# Valid 64-char lowercase hex SHA-256 fingerprints (deterministic test
# constants — not derived from any real envelope).
_VALID_FP_A = "a" * 64
_VALID_FP_B = "b" * 64
_VALID_FP_C = "c" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_synthetic_case(
    *,
    case_id: str = "case-syn",
    source_kind: str = "synthetic_short",
    phase_tags: list[str] | None = None,
    expected_runtime_fixture_fingerprint: str | None = None,
    atomic_facts=None,
) -> ReaderRecordAskR4A3Case:
    """Build a minimal synthetic case for runtime fixture identity tests."""
    expected = ReaderRecordAskR4A3Expected()
    if atomic_facts is not None:
        expected = ReaderRecordAskR4A3Expected(atomic_facts=atomic_facts)
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind=source_kind,
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
        expected=expected,
        phase_tags=phase_tags or [],
        expected_runtime_fixture_fingerprint=(
            expected_runtime_fixture_fingerprint
        ),
    )


def _make_bbc_real_phase1_case(
    *,
    case_id: str = "case-bbc",
    expected_runtime_fixture_fingerprint: str | None = None,
) -> ReaderRecordAskR4A3Case:
    """Build a minimal BBC real_phase1 case."""
    return _make_synthetic_case(
        case_id=case_id,
        source_kind="bbc_record",
        phase_tags=["real_phase1"],
        expected_runtime_fixture_fingerprint=(
            expected_runtime_fixture_fingerprint
        ),
    )


def _make_artifact(
    *,
    case_id: str = "case-syn",
    runtime_fixture_fingerprint: str | None = None,
) -> RawArtifact:
    """Build a minimal RawArtifact for three-layer check tests."""
    return RawArtifact(
        case_id=case_id,
        run_id="phase1-test",
        runtime_fixture_fingerprint=runtime_fixture_fingerprint,
    )


def _make_manifest(
    *,
    runtime_fixture_identities: dict[str, str] | None = None,
    planned_logical_runs: int = 30,
    request_cap: int | None = 40,
    token_cap: int | None = None,
    retry_policy: dict[str, int] | None = None,
    retry_headroom: int | None = 10,
    audit_contract_version: str | None = None,
):
    """Build a minimal manifest-like object for aggregate tests.

    ``audit_contract_version`` selects the manifest version path.
    ``None`` or ``"r4-a4-2r2"`` → V1 (legacy) — the three-layer check
    is skipped. ``"r4-a4-2r3"`` → V2 (strict) — the three-layer check
    is MANDATORY.

    ``retry_policy`` is a dict (V2 typed contract); pass ``None`` for
    the V1-compatible default of ``{}``.
    """
    if retry_policy is None:
        retry_policy = {}
    return SimpleNamespace(
        runtime_fixture_identities=runtime_fixture_identities or {},
        planned_logical_runs=planned_logical_runs,
        request_cap=request_cap,
        token_cap=token_cap,
        retry_policy=retry_policy,
        retry_headroom=retry_headroom,
        audit_contract_version=audit_contract_version,
        planned_run_indices={"case-a": [0, 1, 2]},
        completed_run_indices={"case-a": [0, 1, 2]},
        remaining_run_indices={},
        executed_requests=30,
        executed_tokens=10000,
    )


# ---------------------------------------------------------------------------
# Fingerprint determinism: same fixture assembled twice → same hash
# ---------------------------------------------------------------------------


class TestRuntimeFixtureFingerprintDeterminism:
    """Two assemblies from the same snapshot produce the same
    ``runtime_fixture_fingerprint``."""

    def test_same_inputs_same_hash(self) -> None:
        """Identical (baseline_status, is_complete, chunks) → same hash."""
        chunks = [(0, "Hello world."), (1, "Second chunk.")]
        fp1 = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks,
        )
        fp2 = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks,
        )
        assert fp1 == fp2
        assert is_valid_runtime_fixture_fingerprint(fp1)

    def test_different_chunk_objects_same_content_same_hash(self) -> None:
        """Different list objects with same content → same hash
        (the function does NOT depend on object identity)."""
        chunks1 = [(0, "Hello"), (1, "World")]
        chunks2 = [(0, "Hello"), (1, "World")]
        fp1 = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks1,
        )
        fp2 = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks2,
        )
        assert fp1 == fp2

    def test_chunks_passed_in_different_order_same_hash(self) -> None:
        """Chunks passed out of ordinal order → same hash (function
        sorts by chunk_ordinal internally)."""
        chunks_sorted = [(0, "First"), (1, "Second"), (2, "Third")]
        chunks_shuffled = [(2, "Third"), (0, "First"), (1, "Second")]
        fp1 = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_sorted,
        )
        fp2 = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_shuffled,
        )
        assert fp1 == fp2

    def test_empty_chunks_deterministic(self) -> None:
        """Empty chunks list → deterministic hash."""
        fp1 = compute_runtime_fixture_fingerprint(
            baseline_status="no_units",
            is_complete=False,
            chunks=[],
        )
        fp2 = compute_runtime_fixture_fingerprint(
            baseline_status="no_units",
            is_complete=False,
            chunks=[],
        )
        assert fp1 == fp2


# ---------------------------------------------------------------------------
# Fingerprint sensitivity: text/order/truncation/coverage change → hash
# changes
# ---------------------------------------------------------------------------


class TestRuntimeFixtureFingerprintSensitivity:
    """Any change to text/order/truncation/coverage produces a DIFFERENT
    hash."""

    def test_text_change_changes_hash(self) -> None:
        """Changing chunk text → different hash."""
        chunks_a = [(0, "Hello world.")]
        chunks_b = [(0, "Hello universe.")]
        fp_a = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_a,
        )
        fp_b = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_b,
        )
        assert fp_a != fp_b

    def test_order_change_changes_hash(self) -> None:
        """Swapping chunk text between ordinals → different hash
        (even though the SET of texts is the same)."""
        chunks_a = [(0, "First"), (1, "Second")]
        chunks_b = [(0, "Second"), (1, "First")]
        fp_a = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_a,
        )
        fp_b = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_b,
        )
        assert fp_a != fp_b

    def test_truncation_changes_hash(self) -> None:
        """Truncating chunk text → different hash."""
        chunks_full = [(0, "The quick brown fox jumps over the lazy dog.")]
        chunks_truncated = [(0, "The quick brown fox.")]
        fp_full = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_full,
        )
        fp_truncated = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_truncated,
        )
        assert fp_full != fp_truncated

    def test_coverage_change_changes_hash(self) -> None:
        """Changing is_complete → different hash (coverage signal)."""
        chunks = [(0, "Hello world.")]
        fp_complete = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks,
        )
        fp_partial = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=False,
            chunks=chunks,
        )
        assert fp_complete != fp_partial

    def test_status_change_changes_hash(self) -> None:
        """Changing baseline_status → different hash."""
        chunks = [(0, "Hello world.")]
        fp_injected = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks,
        )
        fp_mismatch = compute_runtime_fixture_fingerprint(
            baseline_status="envelope_mismatch",
            is_complete=True,
            chunks=chunks,
        )
        assert fp_injected != fp_mismatch

    def test_chunk_count_change_changes_hash(self) -> None:
        """Adding/removing a chunk → different hash."""
        chunks_one = [(0, "Hello")]
        chunks_two = [(0, "Hello"), (1, "World")]
        fp_one = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_one,
        )
        fp_two = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_two,
        )
        assert fp_one != fp_two

    def test_ordinal_change_changes_hash(self) -> None:
        """Changing chunk_ordinal → different hash (even with same
        text)."""
        chunks_a = [(0, "Hello")]
        chunks_b = [(1, "Hello")]
        fp_a = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_a,
        )
        fp_b = compute_runtime_fixture_fingerprint(
            baseline_status="injected",
            is_complete=True,
            chunks=chunks_b,
        )
        assert fp_a != fp_b


# ---------------------------------------------------------------------------
# Missing expected identity on a real_phase1 case → fail-closed
# ---------------------------------------------------------------------------


class TestMissingExpectedRuntimeFingerprintFailsClosed:
    """A real_phase1 case without ``expected_runtime_fixture_fingerprint``
    fails closed: the aggregate three-layer check returns a mismatch with
    reason ``missing_dataset_expected`` (defense-in-depth behind the
    harness preflight)."""

    def test_bbc_missing_expected_returns_mismatch(self) -> None:
        """BBC real_phase1 case with None expected →
        ``missing_dataset_expected``."""
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=None,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_A},
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is True
        assert reason == "missing_dataset_expected"

    def test_bbc_empty_expected_returns_mismatch(self) -> None:
        """BBC real_phase1 case with empty string expected →
        ``missing_dataset_expected``."""
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint="",
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_A},
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is True
        assert reason == "missing_dataset_expected"

    def test_non_bbc_missing_expected_no_mismatch(self) -> None:
        """A case that is NOT real_phase1 (e.g. ``phase_tags=[]`` or
        ``phase_tags=["offline_only"]``) without
        expected_runtime_fixture_fingerprint → backwards-compat, no
        mismatch. Only real_phase1 cases (BBC + synthetic) are required
        to declare the field."""
        case = _make_synthetic_case(
            case_id="case-syn",
            source_kind="synthetic_short",
            phase_tags=[],  # NOT real_phase1 — not required to declare
            expected_runtime_fixture_fingerprint=None,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_A},
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is False
        assert reason is None

    def test_synthetic_real_phase1_missing_expected_returns_mismatch(self) -> None:
        """A synthetic case tagged ``real_phase1`` without
        ``expected_runtime_fixture_fingerprint`` → ``missing_dataset_expected``.

        The declaration requirement covers ALL real_phase1 cases
        (including synthetic), not just BBC.
        """
        case = _make_synthetic_case(
            case_id="case-syn",
            source_kind="synthetic_short",
            phase_tags=["real_phase1"],  # synthetic + real_phase1
            expected_runtime_fixture_fingerprint=None,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_A},
            audit_contract_version="r4-a4-2r3",
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is True
        assert reason == "missing_dataset_expected"


# ---------------------------------------------------------------------------
# Identity mismatch → aggregate verdict blocked (precedence row 5.6)
# ---------------------------------------------------------------------------


class TestRuntimeFingerprintMismatchBlocksEvaluation:
    """``dataset_artifact_mismatch`` → aggregate blocks via precedence
    row 5.6 in ``_decide_final_verdict``."""

    def test_dataset_artifact_mismatch_returns_mismatch(self) -> None:
        """Artifact actual != dataset expected →
        ``dataset_artifact_mismatch``."""
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_B,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_B},
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is True
        assert reason == "dataset_artifact_mismatch"

    def test_missing_artifact_actual_returns_mismatch(self) -> None:
        """Artifact missing runtime_fixture_fingerprint →
        ``missing_artifact_actual``."""
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=None,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_A},
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is True
        assert reason == "missing_artifact_actual"

    def test_verdict_row_5_6_blocks_on_mismatch(self) -> None:
        """Precedence row 5.6: runtime_fixture_identity_mismatch_count > 0
        → ``blocked_incomplete_real_model_run``."""
        from claread_eval.reader_record_ask.run_manifest import (
            CoverageAuditResult,
        )

        coverage_audit = CoverageAuditResult(
            manifest_present=True,
            manifest_status="completed",
            planned_count=1,
            completed_count=1,
            missing_count=0,
            duplicate_count=0,
            unexpected_count=0,
            identity_mismatch_count=0,
            evaluable_artifact_count=1,
            dataset_identity=("ds", "v1", "sha"),
            missing_run_indices={},
            duplicate_run_indices={},
            unexpected_run_indices={},
            manifest_state="valid",
            manifest_run_id_matches=True,
        )
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=coverage_audit,
            identity_mismatched_count=0,
            runtime_identity_mismatch_count=0,
            runtime_fixture_identity_mismatch_count=1,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
            readiness=None,
        )
        assert verdict == "blocked_incomplete_real_model_run"
        assert allow_a4 is False
        assert allow_b1 is False

    def test_verdict_passes_when_no_mismatch(self) -> None:
        """When runtime_fixture_identity_mismatch_count=0, precedence
        row 5.6 does NOT fire (falls through to subsequent rows)."""
        from claread_eval.reader_record_ask.run_manifest import (
            CoverageAuditResult,
        )

        coverage_audit = CoverageAuditResult(
            manifest_present=True,
            manifest_status="completed",
            planned_count=1,
            completed_count=1,
            missing_count=0,
            duplicate_count=0,
            unexpected_count=0,
            identity_mismatch_count=0,
            evaluable_artifact_count=1,
            dataset_identity=("ds", "v1", "sha"),
            missing_run_indices={},
            duplicate_run_indices={},
            unexpected_run_indices={},
            manifest_state="valid",
            manifest_run_id_matches=True,
        )
        # real_model_blocked=True forces fallback to
        # blocked_by_real_model_run (row 10), proving row 5.6 did NOT
        # fire when count=0.
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=coverage_audit,
            identity_mismatched_count=0,
            runtime_identity_mismatch_count=0,
            runtime_fixture_identity_mismatch_count=0,
            real_model_blocked=True,
            has_budget_exhausted=False,
            total_artifacts_loaded=0,
            readiness=None,
        )
        assert verdict == "blocked_by_real_model_run"


# ---------------------------------------------------------------------------
# Three-layer identity consistency (dataset expected == manifest
# identity == artifact actual)
# ---------------------------------------------------------------------------


class TestRuntimeFixtureIdentityConsistency:
    """Three-layer check: dataset expected == manifest identity ==
    artifact actual. Any inconsistency → typed blocker.

    The three-layer check only fires for V2 manifests
    (``audit_contract_version == "r4-a4-2r3"``). V1 manifests skip it
    (backwards-compat — selected by EXPLICIT version, NOT by empty-dict
    guessing). Tests below set ``audit_contract_version`` explicitly to
    exercise the V2 path.
    """

    def test_manifest_artifact_mismatch(self) -> None:
        """V2 manifest: manifest identity != artifact actual →
        ``manifest_artifact_mismatch``."""
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_B},
            audit_contract_version="r4-a4-2r3",
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is True
        assert reason == "manifest_artifact_mismatch"

    def test_dataset_manifest_mismatch(self) -> None:
        """Manifest identity != dataset expected →
        ``dataset_manifest_mismatch``.

        Note: this requires artifact actual == manifest identity (so
        check 4 passes) but both differ from dataset expected. We
        construct: dataset=A, artifact=B, manifest=B. Check 3
        (dataset_artifact_mismatch) fires first because artifact=B !=
        dataset=A. To test dataset_manifest_mismatch specifically, we
        need artifact==manifest but both != dataset, but check 3 fires
        first. So this test verifies the precedence: check 3 catches
        it before check 5.
        """
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_B,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_B},
            audit_contract_version="r4-a4-2r3",
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        # Check 3 fires first: dataset=A, artifact=B → mismatch.
        assert mismatch is True
        assert reason == "dataset_artifact_mismatch"

    def test_manifest_identity_missing(self) -> None:
        """V2 manifest that does not contain this case's identity →
        ``manifest_identity_missing``.

        V2 manifests MUST carry identity for every planned case
        (enforced at parse time). Defense-in-depth: if a V2 manifest
        reaches this function with a missing identity,
        ``manifest_identity_missing`` is returned.
        """
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        # V2 manifest with empty identity map — corrupt (would have
        # been rejected at parse time). Defense-in-depth check fires.
        manifest = _make_manifest(
            runtime_fixture_identities={},
            audit_contract_version="r4-a4-2r3",
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is True
        assert reason == "manifest_identity_missing"

    def test_manifest_identity_foreign(self) -> None:
        """V2 manifest: manifest identity is valid SHA-256 but matches
        neither dataset expected nor artifact actual. Check 3 fires
        first (dataset_artifact_mismatch) because artifact=A, dataset=A,
        manifest=C. Actually: dataset=A, artifact=A → check 3 passes.
        Then manifest=C != artifact=A → check 4 fires
        (manifest_artifact_mismatch)."""
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_C},
            audit_contract_version="r4-a4-2r3",
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is True
        assert reason == "manifest_artifact_mismatch"

    def test_all_three_match_no_mismatch(self) -> None:
        """V2 manifest: dataset expected == manifest identity ==
        artifact actual → no mismatch (happy path)."""
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_A},
            audit_contract_version="r4-a4-2r3",
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is False
        assert reason is None

    def test_pre_v2_manifest_backwards_compat(self) -> None:
        """A V1 manifest (``audit_contract_version=None`` or
        ``"r4-a4-2r2"``) skips the three-layer check. This is selected
        by EXPLICIT version, NOT by empty-dict guessing.

        Construct: dataset=A, artifact=A, manifest=B. Under V2 this
        would be ``manifest_artifact_mismatch``. Under V1 (no
        ``audit_contract_version``) the three-layer check is skipped
        → no mismatch.
        """
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        # V1 manifest (audit_contract_version=None) — three-layer
        # check skipped even though manifest identity differs from
        # artifact actual.
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_B},
            audit_contract_version=None,
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is False
        assert reason is None

    def test_v1_explicit_version_skips_three_layer_check(self) -> None:
        """A manifest with ``audit_contract_version="r4-a4-2r2"``
        (explicit V1) also skips the three-layer check.

        This proves V1 compat is selected by EXPLICIT version, not
        by ``None`` default.
        """
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        manifest = _make_manifest(
            runtime_fixture_identities={case.id: _VALID_FP_B},
            audit_contract_version="r4-a4-2r2",  # explicit V1
        )
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, manifest
        )
        assert mismatch is False
        assert reason is None

    def test_no_manifest_skips_three_layer_check(self) -> None:
        """No manifest at all → only checks 1-3 apply."""
        case = _make_bbc_real_phase1_case(
            expected_runtime_fixture_fingerprint=_VALID_FP_A,
        )
        artifact = _make_artifact(
            case_id=case.id,
            runtime_fixture_fingerprint=_VALID_FP_A,
        )
        cases_by_id = {case.id: case}
        mismatch, reason = _RUNNER._runtime_fixture_identity_mismatches(
            artifact, cases_by_id, None
        )
        assert mismatch is False
        assert reason is None


# ---------------------------------------------------------------------------
# Required-facts precheck: required fact not supported by fixture →
# flagged
# ---------------------------------------------------------------------------


class TestRequiredFactSupportPrecheck:
    """``precheck_required_facts_support`` returns a non-empty list when
    a required fact has no supporting chunk."""

    def test_supported_fact_returns_empty(self) -> None:
        """Required fact with alias in chunk text → supported (empty
        list)."""
        atomic_facts = [
            ("fact-1", ("春日阅读节",), True),
        ]
        chunks = [(0, "城南社区举办了春日阅读节。")]
        unsupported = precheck_required_facts_support(
            atomic_facts=atomic_facts,
            chunks=chunks,
        )
        assert unsupported == []

    def test_unsupported_fact_returns_fact_id(self) -> None:
        """Required fact with alias NOT in any chunk → unsupported."""
        atomic_facts = [
            ("fact-1", ("不存在的关键词",), True),
        ]
        chunks = [(0, "城南社区举办了春日阅读节。")]
        unsupported = precheck_required_facts_support(
            atomic_facts=atomic_facts,
            chunks=chunks,
        )
        assert unsupported == ["fact-1"]

    def test_optional_fact_unsupported_returns_empty(self) -> None:
        """Optional (required=False) fact unsupported → not flagged."""
        atomic_facts = [
            ("fact-1", ("不存在的关键词",), False),
        ]
        chunks = [(0, "城南社区举办了春日阅读节。")]
        unsupported = precheck_required_facts_support(
            atomic_facts=atomic_facts,
            chunks=chunks,
        )
        assert unsupported == []

    def test_metadata_only_fact_returns_empty(self) -> None:
        """Required fact with empty source_aliases → skipped
        (metadata-only)."""
        atomic_facts = [
            ("fact-1", (), True),
        ]
        chunks = [(0, "Some text.")]
        unsupported = precheck_required_facts_support(
            atomic_facts=atomic_facts,
            chunks=chunks,
        )
        assert unsupported == []

    def test_empty_chunks_all_unsupported(self) -> None:
        """Empty chunks → all required facts with aliases unsupported."""
        atomic_facts = [
            ("fact-1", ("keyword",), True),
            ("fact-2", ("another",), True),
        ]
        chunks = []
        unsupported = precheck_required_facts_support(
            atomic_facts=atomic_facts,
            chunks=chunks,
        )
        assert unsupported == ["fact-1", "fact-2"]

    def test_case_insensitive_match(self) -> None:
        """Case-insensitive substring match."""
        atomic_facts = [
            ("fact-1", ("HELLO",), True),
        ]
        chunks = [(0, "hello world")]
        unsupported = precheck_required_facts_support(
            atomic_facts=atomic_facts,
            chunks=chunks,
        )
        assert unsupported == []

    def test_multiple_aliases_any_match_suffices(self) -> None:
        """Multiple aliases in a fact — any match suffices."""
        atomic_facts = [
            ("fact-1", ("missing", "春日阅读节", "absent"), True),
        ]
        chunks = [(0, "城南社区举办了春日阅读节。")]
        unsupported = precheck_required_facts_support(
            atomic_facts=atomic_facts,
            chunks=chunks,
        )
        assert unsupported == []

# ---------------------------------------------------------------------------
# Budget semantics read from the manifest, not the current shell env
# ---------------------------------------------------------------------------


class TestBudgetSemanticsUsesManifest:
    """``_compute_budget_semantics`` reads ``planned_logical_runs`` and
    ``request_cap`` from the manifest, NOT from the current shell env."""

    def test_reads_planned_logical_runs_from_manifest(self) -> None:
        """Manifest with planned_logical_runs=30 → budget_semantics
        uses 30 (not from env)."""
        manifest = _make_manifest(
            planned_logical_runs=30,
            request_cap=40,
        )
        budget = _RUNNER._compute_budget_semantics(
            manifest=manifest,
            request_cap=None,  # env cap = None
        )
        assert budget.planned_logical_runs == 30
        assert budget.request_cap == 40
        assert budget.retry_headroom == 10  # 40 - 30

    def test_reads_request_cap_from_manifest_not_env(self) -> None:
        """Manifest with request_cap=40, env cap=50 → budget_semantics
        uses manifest's 40 (NOT env's 50)."""
        manifest = _make_manifest(
            planned_logical_runs=30,
            request_cap=40,
        )
        budget = _RUNNER._compute_budget_semantics(
            manifest=manifest,
            request_cap=50,  # env cap — should be IGNORED
        )
        assert budget.request_cap == 40  # manifest wins
        assert budget.retry_headroom == 10  # 40 - 30

    def test_falls_back_to_env_when_manifest_missing_request_cap(
        self,
    ) -> None:
        """Legacy manifest without request_cap → falls back to
        env cap (backwards compat)."""
        manifest = SimpleNamespace(
            planned_logical_runs=0,  # legacy manifest
            request_cap=None,  # manifest field absent
            planned_run_indices={"case-a": [0, 1, 2]},
            completed_run_indices={"case-a": [0, 1, 2]},
            remaining_run_indices={},
            executed_requests=3,
            executed_tokens=100,
        )
        budget = _RUNNER._compute_budget_semantics(
            manifest=manifest,
            request_cap=50,  # env cap — used as fallback
        )
        # planned_logical_runs falls back to sum of planned_run_indices
        assert budget.planned_logical_runs == 3
        # request_cap falls back to env
        assert budget.request_cap == 50

    def test_no_manifest_uses_env_cap(self) -> None:
        """No manifest at all → env cap used, planned=0."""
        budget = _RUNNER._compute_budget_semantics(
            manifest=None,
            request_cap=50,
        )
        assert budget.planned_logical_runs == 0
        assert budget.request_cap == 50
        assert budget.retry_headroom == 50  # 50 - 0

    def test_retries_consumed_calculation(self) -> None:
        """retries_consumed = executed_requests - actual_completed_runs."""
        manifest = SimpleNamespace(
            planned_logical_runs=30,
            request_cap=40,
            planned_run_indices={"case-a": [0, 1, 2]},
            completed_run_indices={"case-a": [0, 1]},  # 2 completed
            remaining_run_indices={"case-a": [2]},
            executed_requests=5,  # 5 requests for 2 completed → 3 retries
            executed_tokens=1000,
        )
        budget = _RUNNER._compute_budget_semantics(
            manifest=manifest,
            request_cap=None,
        )
        assert budget.actual_completed_runs == 2
        assert budget.retries_consumed == 3  # 5 - 2

    def test_planned_logical_runs_field_wins_over_recompute(self) -> None:
        """When manifest has both planned_logical_runs field AND
        planned_run_indices, the field wins."""
        manifest = SimpleNamespace(
            planned_logical_runs=30,  # explicit field
            request_cap=40,
            planned_run_indices={"case-a": [0, 1, 2]},  # sum = 3
            completed_run_indices={"case-a": [0, 1, 2]},
            remaining_run_indices={},
            executed_requests=30,
            executed_tokens=1000,
        )
        budget = _RUNNER._compute_budget_semantics(
            manifest=manifest,
            request_cap=None,
        )
        # Field wins (30), not recompute (3)
        assert budget.planned_logical_runs == 30


# ---------------------------------------------------------------------------
# Module surface: runner and runtime_fixture expose the contract
# functions
# ---------------------------------------------------------------------------


class TestRuntimeFixtureModuleSurface:
    """The runner script and the ``runtime_fixture`` module import
    cleanly and expose the contract functions used by the harness."""

    def test_runner_module_imports(self) -> None:
        """The runner script imports cleanly (no syntax errors)."""
        assert _RUNNER is not None
        assert hasattr(_RUNNER, "_decide_final_verdict")
        assert hasattr(_RUNNER, "_runtime_fixture_identity_mismatches")
        assert hasattr(_RUNNER, "_compute_budget_semantics")

    def test_runtime_fixture_module_imports(self) -> None:
        """The runtime_fixture module imports cleanly."""
        from claread_eval.reader_record_ask import runtime_fixture
        assert hasattr(runtime_fixture, "compute_runtime_fixture_fingerprint")
        assert hasattr(runtime_fixture, "precheck_required_facts_support")
        assert hasattr(runtime_fixture, "is_valid_runtime_fixture_fingerprint")

    def test_pattern_is_valid_regex(self) -> None:
        """RUNTIME_FIXTURE_FINGERPRINT_PATTERN is a valid regex."""
        import re
        pattern = re.compile(RUNTIME_FIXTURE_FINGERPRINT_PATTERN)
        assert pattern.match(_VALID_FP_A)
        assert not pattern.match("X" * 64)
        assert not pattern.match("a" * 63)
        assert not pattern.match("a" * 65)

    def test_is_valid_runtime_fixture_fingerprint(self) -> None:
        """is_valid_runtime_fixture_fingerprint accepts valid and
        rejects invalid."""
        assert is_valid_runtime_fixture_fingerprint(_VALID_FP_A)
        assert not is_valid_runtime_fixture_fingerprint(None)
        assert not is_valid_runtime_fixture_fingerprint("")
        assert not is_valid_runtime_fixture_fingerprint("X" * 64)
        assert not is_valid_runtime_fixture_fingerprint("a" * 63)
