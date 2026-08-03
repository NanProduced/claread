"""R4-A4-2R2 — Runtime Fixture Identity 最终闭合 tests.

Spec: R4-A4-2R2 — Runtime Fixture Identity 最终闭合.

Task contract (10 required test scenarios):

1. Same fixture assembled twice → same hash (determinism).
2. text/order/truncation/coverage change → hash changes (sensitivity).
3. BBC real_phase1 case missing expected_runtime_fixture_fingerprint
   → builder=0, provider=0 (fail-closed at preflight).
4. Mismatch (computed != expected) → calls=0 (fail-closed at preflight).
5. artifact/manifest/dataset identity inconsistency → aggregate blocked
   (three-layer check via _runtime_fixture_identity_mismatches).
6. Required atomic fact not supported by fixture → calls=0
   (precheck_required_facts_support returns non-empty list).
7. Phase 1 planner restores 10 cases × 3 reps = 30 logical runs.
8. Synthetic absent-year and publish-date cases do not contain year
   tokens in their expected answer (must_declare_no_year=True,
   allowed_temporal_claims=[]).
9. Aggregate budget audit does not depend on current shell env
   (_compute_budget_semantics reads from manifest, not env).
10. Full reader_record_ask eval tests + ruff + git diff --check pass
    (verified via separate command runs, not in this file).

No real LLM / provider calls. All tests are deterministic and use
mocks / minimal pydantic models / the actual dataset on disk.
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
_DATASET_DIR = (
    _REPO_ROOT / "evals" / "tmp" / "reader-record-ask-r4-a3"
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

    R4-A4-2R3: ``audit_contract_version`` selects the manifest version
    path. ``None`` or ``"r4-a4-2r2"`` → V1 (legacy) — checks 4-7
    (three-layer) are skipped. ``"r4-a4-2r3"`` → V2 (strict) — the
    three-layer check is MANDATORY.

    ``retry_policy`` is now a dict (V2 typed contract) — the legacy
    string ``"default"`` is no longer accepted by the dataclass. Tests
    that need V1 backwards-compat for the legacy string can pass
    ``retry_policy=None`` and the helper defaults to ``{}``.
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
# Scenario 1: Same fixture assembled twice → same hash (determinism)
# ---------------------------------------------------------------------------


class TestScenario1Determinism:
    """Scenario 1: two assemblies from the same snapshot produce the
    same ``runtime_fixture_fingerprint``."""

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
# Scenario 2: text/order/truncation/coverage change → hash changes
# ---------------------------------------------------------------------------


class TestScenario2Sensitivity:
    """Scenario 2: any change to text/order/truncation/coverage
    produces a DIFFERENT hash."""

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
# Scenario 3: BBC real_phase1 case missing expected identity →
# builder=0, provider=0
# ---------------------------------------------------------------------------


class TestScenario3BbcMissingExpected:
    """Scenario 3: BBC real_phase1 case without
    ``expected_runtime_fixture_fingerprint`` → aggregate three-layer
    check returns mismatch with reason ``missing_dataset_expected``.

    The harness preflight also fail-closes on this (tested in the
    harness test file via ``pytest.skip``). Here we test the aggregate
    post-call helper to verify the defense-in-depth check.
    """

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
        """R4-A4-2R3 P0-2: a case that is NOT real_phase1 (e.g.
        ``phase_tags=[]`` or ``phase_tags=["offline_only"]``) without
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
        """R4-A4-2R3 P0-2: synthetic case tagged ``real_phase1`` without
        ``expected_runtime_fixture_fingerprint`` → ``missing_dataset_expected``.

        This is the key R4-A4-2R3 expansion: the requirement now covers
        ALL real_phase1 cases (including synthetic), not just BBC.
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
# Scenario 4: Mismatch → calls=0 (aggregate blocks via precedence 5.6)
# ---------------------------------------------------------------------------


class TestScenario4MismatchBlocks:
    """Scenario 4: dataset_artifact_mismatch → aggregate blocks via
    precedence row 5.6 in ``_decide_final_verdict``."""

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
# Scenario 5: artifact/manifest/dataset identity inconsistency →
# aggregate blocked
# ---------------------------------------------------------------------------


class TestScenario5ThreeLayerInconsistency:
    """Scenario 5: three-layer check (dataset expected == manifest
    identity == artifact actual). Any inconsistency → typed blocker.

    R4-A4-2R3: the three-layer check only fires for V2 manifests
    (``audit_contract_version == "r4-a4-2r3"``). V1 manifests skip
    checks 4-7 (backwards-compat — selected by EXPLICIT version, NOT
    by empty-dict guessing). Tests below set ``audit_contract_version``
    explicitly to exercise the V2 path.
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

        R4-A4-2R3: V2 manifests MUST carry identity for every planned
        case (Rule 18b enforced at parse time). Defense-in-depth: if
        a V2 manifest reaches this function with a missing identity,
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
        # V2 manifest with empty identity map — corrupt (Rule 18a
        # would have rejected this at parse time). Defense-in-depth
        # check 6 fires.
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
        """R4-A4-2R3: a V1 manifest (``audit_contract_version=None``
        or ``"r4-a4-2r2"``) skips the three-layer check — checks 4-7
        do NOT fire. This is selected by EXPLICIT version, NOT by
        empty-dict guessing.

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
        """R4-A4-2R3: a manifest with ``audit_contract_version=
        "r4-a4-2r2"`` (explicit V1) also skips the three-layer check.

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
# Scenario 6: Required fact not supported by fixture → calls=0
# ---------------------------------------------------------------------------


class TestScenario6RequiredFactUnsupported:
    """Scenario 6: ``precheck_required_facts_support`` returns
    non-empty list when a required fact has no supporting chunk."""

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
# Scenario 7: Phase 1 planner restores 10 cases × 3 reps = 30 runs
# ---------------------------------------------------------------------------


class TestScenario7PhasePlannerRestores30Runs:
    """Scenario 7: PhasePlanner with the actual dataset produces 10
    cases × 3 repetitions = 30 logical runs."""

    def test_phase1_has_10_cases(self) -> None:
        """Phase 1 selects exactly 10 real_phase1 cases."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )
        from claread_eval.reader_record_ask.phase_planner import PhasePlanner

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        planner = PhasePlanner(
            dataset=snapshot.dataset,
            phase=1,
            repetitions=3,
        )
        cases = planner.cases_to_run
        assert len(cases) == 10

    def test_phase1_planned_logical_runs_is_30(self) -> None:
        """PhasePlanner.planned_logical_runs == 30 (10 cases × 3
        reps)."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )
        from claread_eval.reader_record_ask.phase_planner import PhasePlanner

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        planner = PhasePlanner(
            dataset=snapshot.dataset,
            phase=1,
            repetitions=3,
        )
        assert planner.planned_logical_runs == 30

    def test_phase1_repetitions_is_3(self) -> None:
        """Phase 1 repetitions == 3 (default)."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )
        from claread_eval.reader_record_ask.phase_planner import PhasePlanner

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        planner = PhasePlanner(
            dataset=snapshot.dataset,
            phase=1,
        )
        assert planner.repetitions == 3

    def test_phase1_includes_syn_absent_year(self) -> None:
        """Phase 1 includes the synthetic absent-year case."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )
        from claread_eval.reader_record_ask.phase_planner import PhasePlanner

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        planner = PhasePlanner(
            dataset=snapshot.dataset,
            phase=1,
            repetitions=3,
        )
        case_ids = {c.id for c in planner.cases_to_run}
        assert "syn-absent-year" in case_ids

    def test_phase1_includes_syn_publish_date_unknown(self) -> None:
        """Phase 1 includes the new synthetic publish-date-unknown
        case."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )
        from claread_eval.reader_record_ask.phase_planner import PhasePlanner

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        planner = PhasePlanner(
            dataset=snapshot.dataset,
            phase=1,
            repetitions=3,
        )
        case_ids = {c.id for c in planner.cases_to_run}
        assert "syn-publish-date-unknown" in case_ids

    def test_phase1_excludes_offline_only(self) -> None:
        """Phase 1 excludes offline_only cases (BBC publish-date/
        absent-year stay offline-only)."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )
        from claread_eval.reader_record_ask.phase_planner import PhasePlanner

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        planner = PhasePlanner(
            dataset=snapshot.dataset,
            phase=1,
            repetitions=3,
        )
        case_ids = {c.id for c in planner.cases_to_run}
        assert "bbc-publish-date-unknown" not in case_ids
        assert "bbc-absent-year-unknown" not in case_ids


# ---------------------------------------------------------------------------
# Scenario 8: synthetic absent-year and publish-date do not contain
# year in expected answer (must_declare_no_year=True,
# allowed_temporal_claims=[])
# ---------------------------------------------------------------------------


class TestScenario8SyntheticCasesNoYear:
    """Scenario 8: synthetic absent-year and publish-date-unknown cases
    have ``must_declare_no_year=True`` and
    ``allowed_temporal_claims=[]`` — the expected answer must NOT
    contain year tokens."""

    def test_syn_absent_year_must_declare_no_year(self) -> None:
        """syn-absent-year has must_declare_no_year=True."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        cases_by_id = {c.id: c for c in snapshot.dataset.cases}
        case = cases_by_id["syn-absent-year"]
        assert case.expected.must_declare_no_year is True
        assert case.expected.allowed_temporal_claims == []

    def test_syn_publish_date_must_declare_no_year(self) -> None:
        """syn-publish-date-unknown has must_declare_no_year=True."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        cases_by_id = {c.id: c for c in snapshot.dataset.cases}
        case = cases_by_id["syn-publish-date-unknown"]
        assert case.expected.must_declare_no_year is True
        assert case.expected.allowed_temporal_claims == []

    def test_syn_absent_year_article_has_no_year(self) -> None:
        """syn-absent-year article text does NOT contain year tokens
        (pure scene description)."""
        import re

        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )

        YEAR_RE = re.compile(r"(?<![0-9])(?:19|20)\d{2}(?![0-9])\s*年?")
        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        cases_by_id = {c.id: c for c in snapshot.dataset.cases}
        case = cases_by_id["syn-absent-year"]
        matches = YEAR_RE.findall(case.article_text or "")
        assert matches == [], (
            f"syn-absent-year article should have NO year tokens, "
            f"found: {matches}"
        )

    def test_syn_publish_date_article_has_event_year_but_no_publish_date(
        self,
    ) -> None:
        """syn-publish-date-unknown article HAS event year (2024) but
        does NOT contain publish-date indicators. The event year must
        NOT be treated as the publish date."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        cases_by_id = {c.id: c for c in snapshot.dataset.cases}
        case = cases_by_id["syn-publish-date-unknown"]
        # Article contains event year 2024
        assert "2024" in (case.article_text or "")
        # Article does NOT contain publish-date indicators
        article = case.article_text or ""
        publish_indicators = [
            "发布于", "刊登于", "刊发于", "报道于",
            "发布日期", "刊登日期", "见报日期",
        ]
        for indicator in publish_indicators:
            assert indicator not in article, (
                f"syn-publish-date-unknown article should NOT contain "
                f"publish-date indicator {indicator!r}"
            )
        # forbidden_answer_patterns blocks treating 2024 as publish date
        assert len(case.expected.forbidden_answer_patterns) > 0

    def test_syn_publish_date_forbidden_patterns_block_event_year_as_publish(
        self,
    ) -> None:
        """syn-publish-date-unknown forbidden_answer_patterns block
        patterns that would treat event year 2024 as publish date."""
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )

        snapshot = load_r4_a3_dataset_with_snapshot(_DATASET_DIR)
        cases_by_id = {c.id: c for c in snapshot.dataset.cases}
        case = cases_by_id["syn-publish-date-unknown"]
        forbidden = case.expected.forbidden_answer_patterns
        # At least one pattern must block "2024" + publish indicator
        has_2024_blocker = any("2024" in p for p in forbidden)
        assert has_2024_blocker, (
            f"forbidden_answer_patterns must block 2024-as-publish-date; "
            f"got: {forbidden}"
        )


# ---------------------------------------------------------------------------
# Scenario 9: Aggregate budget audit does not depend on current env
# ---------------------------------------------------------------------------


class TestScenario9BudgetSelfContained:
    """Scenario 9: ``_compute_budget_semantics`` reads
    ``planned_logical_runs`` and ``request_cap`` from the manifest,
    NOT from the current shell env."""

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
        """Pre-R4-A4-2R2 manifest without request_cap → falls back to
        env cap (backwards compat)."""
        manifest = SimpleNamespace(
            planned_logical_runs=0,  # pre-R4-A4-2R2
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
# Scenario 10: full test suite + ruff + git diff --check
# ---------------------------------------------------------------------------


class TestScenario10VerificationCommands:
    """Scenario 10: verification commands are documented. The actual
    command execution is done outside this test file (via the
    ``verify`` todo step) to avoid coupling test pass/fail to shell
    availability.

    The tests below verify that the modified files are syntactically
    valid Python (importable) — a minimum bar for ruff/typecheck.
    """

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
