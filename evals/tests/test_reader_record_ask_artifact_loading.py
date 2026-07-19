"""Adversarial tests for R4-A3 artifact audit boundary closure (P0-1 + P0-2).

Spec: `.trae/specs/audit-r4-a3-eval-harness-final-closure/spec.md`

This module closes 4 adjacent boundaries in one pass:

1. **P0-1 Artifact strict schema** — ``RawArtifact`` audit-critical fields
   use ``Strict*`` types + format validators so Pydantic coercion CANNOT
   turn ``run_index=True`` into ``1``, ``budget_exhausted="false"`` into
   ``False``, or accept malformed dataset identity SHAs.

2. **P0-1 Artifact load audit** — :func:`load_artifacts_with_audit`
   produces a typed :class:`ArtifactLoadResult` with counts for
   invalid_json / invalid_schema / foreign_run_id. Corrupt/invalid/
   foreign artifacts are COUNTED, not silently dropped.

3. **P0-2 Dataset case binding** — manifest planned case IDs MUST be a
   subset of the current dataset's cases_by_id. Artifacts referencing
   unknown cases are counted (``unknown_artifact_case_count``) and
   force ``blocked_incomplete_real_model_run``.

4. **P0-2 Evaluation completeness** — :class:`AggregateReadinessAudit`
   is the single source of truth for normal-verdict readiness.
   ``_decide_normal_verdict([])`` MUST NOT return ``accepted``
   (structural fix for the ``all([]) → True`` bug).

No real LLM / provider calls. All tests are deterministic.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from claread_eval.reader_record_ask.artifact_loading import (
    ArtifactLoadResult,
    load_artifacts_with_audit,
)
from claread_eval.reader_record_ask.evaluators.aggregator import CaseEvalResult
from claread_eval.reader_record_ask.evaluators.artifact import (
    RawArtifact,
    RawEvidenceObservation,
    RawUsage,
)
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.run_manifest import (
    MANIFEST_SCHEMA_VERSION,
    CoverageAuditResult,
    ReaderRecordAskRunManifest,
    write_manifest_atomic,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)
from claread_eval.reader_record_ask.session import RunSessionLayout

# ---------------------------------------------------------------------------
# Load the runner module (host of _decide_final_verdict + aggregate +
# AggregateReadinessAudit).
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


# ---------------------------------------------------------------------------
# Shared constants and helpers
# ---------------------------------------------------------------------------

_VALID_SHA = "a" * 64
_VALID_SHA_B = "b" * 64  # different but still valid 64-hex


def _make_case(
    *,
    case_id: str = "case-a",
    article_text: str = "Hello world.",
) -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
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
        expected=ReaderRecordAskR4A3Expected(),
        tags=[],
        phase_tags=["real_phase1"],
    )


def _write_dataset_dir(
    dataset_dir: Path,
    *,
    cases: list[ReaderRecordAskR4A3Case],
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


def _make_valid_artifact(
    *,
    case_id: str = "case-a",
    run_id: str = "phase1-test",
    run_index: int = 0,
    dataset_id: str | None = "test-dataset",
    dataset_schema_version: str | None = "test-schema-v1",
    dataset_content_sha256: str | None = _VALID_SHA,
    budget_exhausted: bool = False,
) -> RawArtifact:
    """Build a schema-valid RawArtifact (passes all strict validators)."""
    return RawArtifact(
        case_id=case_id,
        run_id=run_id,
        run_index=run_index,
        dataset_id=dataset_id,
        dataset_schema_version=dataset_schema_version,
        dataset_content_sha256=dataset_content_sha256,
        budget_exhausted=budget_exhausted,
    )


def _write_artifact_json(
    artifact_dir: Path,
    filename: str,
    payload: object,
) -> Path:
    """Write a JSON file (potentially invalid) to ``artifact_dir``.

    ``payload`` may be a dict, list, string, or raw bytes — used to
    write corrupt/invalid/foreign JSON files for the load audit tests.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / filename
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    elif isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_passing_case_result(
    *,
    case_id: str = "case-a",
    run_id: str = "phase1-test",
) -> CaseEvalResult:
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


def _make_readiness(
    *,
    artifact_load_clean: bool = True,
    discovered_file_count: int = 1,
    invalid_artifact_count: int = 0,
    manifest_state: str = "valid",
    manifest_present: bool = True,
    manifest_run_id_matches: bool | None = True,
    manifest_status: str | None = "completed",
    manifest_is_complete: bool = True,
    coverage_counts_clean: bool = True,
    planned_count: int = 1,
    evaluable_artifact_count: int = 1,
    unknown_planned_case_count: int = 0,
    unknown_artifact_case_count: int = 0,
    evaluated_case_result_count: int = 1,
    instrumentation_incomplete_count: int = 0,
    legacy_artifact_count: int = 0,
) -> _RUNNER.AggregateReadinessAudit:
    return _RUNNER.AggregateReadinessAudit(
        artifact_load_clean=artifact_load_clean,
        discovered_file_count=discovered_file_count,
        invalid_artifact_count=invalid_artifact_count,
        manifest_state=manifest_state,
        manifest_present=manifest_present,
        manifest_run_id_matches=manifest_run_id_matches,
        manifest_status=manifest_status,
        manifest_is_complete=manifest_is_complete,
        coverage_counts_clean=coverage_counts_clean,
        planned_count=planned_count,
        evaluable_artifact_count=evaluable_artifact_count,
        unknown_planned_case_count=unknown_planned_case_count,
        unknown_artifact_case_count=unknown_artifact_case_count,
        evaluated_case_result_count=evaluated_case_result_count,
        instrumentation_incomplete_count=instrumentation_incomplete_count,
        legacy_artifact_count=legacy_artifact_count,
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


# ===========================================================================
# SECTION 1: RawArtifact strict schema (P0-1)
#
# Audit-critical fields MUST reject Pydantic coercion. Each test below
# feeds a coerced/wrong type and asserts ValidationError is raised.
# ===========================================================================


class TestRawArtifactStrictSchema:
    """P0-1: RawArtifact audit-critical fields reject Pydantic coercion."""

    # ------------------------------------------------------------------
    # run_index strict int (reject bool / str / float / negative)
    # ------------------------------------------------------------------

    def test_run_index_true_rejected(self) -> None:
        """``run_index=True`` MUST be rejected (Pydantic would coerce to 1)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", run_index=True)  # type: ignore[arg-type]

    def test_run_index_false_rejected(self) -> None:
        """``run_index=False`` MUST be rejected (Pydantic would coerce to 0)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", run_index=False)  # type: ignore[arg-type]

    def test_run_index_string_rejected(self) -> None:
        """``run_index="1"`` MUST be rejected (Pydantic would coerce to 1)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", run_index="1")  # type: ignore[arg-type]

    def test_run_index_float_rejected(self) -> None:
        """``run_index=1.0`` MUST be rejected (Pydantic would coerce to 1)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", run_index=1.0)  # type: ignore[arg-type]

    def test_run_index_negative_rejected(self) -> None:
        """``run_index=-1`` MUST be rejected (negative not allowed)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", run_index=-1)

    # ------------------------------------------------------------------
    # case_id / run_id strict non-empty string
    # ------------------------------------------------------------------

    def test_case_id_empty_rejected(self) -> None:
        """``case_id=""`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="", run_id="r")

    def test_case_id_whitespace_rejected(self) -> None:
        """``case_id="   "`` MUST be rejected (whitespace-only)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="   ", run_id="r")

    def test_run_id_empty_rejected(self) -> None:
        """``run_id=""`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="")

    def test_run_id_whitespace_rejected(self) -> None:
        """``run_id="   "`` MUST be rejected (whitespace-only)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="   ")

    # ------------------------------------------------------------------
    # budget_exhausted strict bool (reject str / int)
    # ------------------------------------------------------------------

    def test_budget_exhausted_string_false_rejected(self) -> None:
        """``budget_exhausted="false"`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", budget_exhausted="false")  # type: ignore[arg-type]

    def test_budget_exhausted_string_true_rejected(self) -> None:
        """``budget_exhausted="true"`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", budget_exhausted="true")  # type: ignore[arg-type]

    def test_budget_exhausted_int_zero_rejected(self) -> None:
        """``budget_exhausted=0`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", budget_exhausted=0)  # type: ignore[arg-type]

    def test_budget_exhausted_int_one_rejected(self) -> None:
        """``budget_exhausted=1`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", budget_exhausted=1)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # thinking_enabled strict bool (reject str / int)
    # ------------------------------------------------------------------

    def test_thinking_enabled_string_false_rejected(self) -> None:
        """``thinking_enabled="false"`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", thinking_enabled="false")  # type: ignore[arg-type]

    def test_thinking_enabled_string_true_rejected(self) -> None:
        """``thinking_enabled="true"`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", thinking_enabled="true")  # type: ignore[arg-type]

    def test_thinking_enabled_int_zero_rejected(self) -> None:
        """``thinking_enabled=0`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", thinking_enabled=0)  # type: ignore[arg-type]

    def test_thinking_enabled_int_one_rejected(self) -> None:
        """``thinking_enabled=1`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", thinking_enabled=1)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # executed_requests / executed_tokens strict non-negative int
    # ------------------------------------------------------------------

    def test_executed_requests_bool_rejected(self) -> None:
        """``executed_requests=True`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", executed_requests=True)  # type: ignore[arg-type]

    def test_executed_requests_string_rejected(self) -> None:
        """``executed_requests="3"`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", executed_requests="3")  # type: ignore[arg-type]

    def test_executed_requests_float_rejected(self) -> None:
        """``executed_requests=1.5`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", executed_requests=1.5)  # type: ignore[arg-type]

    def test_executed_requests_negative_rejected(self) -> None:
        """``executed_requests=-1`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", executed_requests=-1)

    def test_executed_tokens_bool_rejected(self) -> None:
        """``executed_tokens=True`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", executed_tokens=True)  # type: ignore[arg-type]

    def test_executed_tokens_string_rejected(self) -> None:
        """``executed_tokens="3"`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", executed_tokens="3")  # type: ignore[arg-type]

    def test_executed_tokens_negative_rejected(self) -> None:
        """``executed_tokens=-1`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", executed_tokens=-1)

    # ------------------------------------------------------------------
    # RawUsage counters strict non-negative int
    # ------------------------------------------------------------------

    def test_raw_usage_requests_bool_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawUsage(requests=True)  # type: ignore[arg-type]

    def test_raw_usage_requests_string_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawUsage(requests="3")  # type: ignore[arg-type]

    def test_raw_usage_requests_float_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawUsage(requests=1.5)  # type: ignore[arg-type]

    def test_raw_usage_requests_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawUsage(requests=-1)

    def test_raw_usage_input_tokens_bool_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawUsage(input_tokens=True)  # type: ignore[arg-type]

    def test_raw_usage_input_tokens_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawUsage(input_tokens=-1)

    def test_raw_usage_output_tokens_bool_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawUsage(output_tokens=True)  # type: ignore[arg-type]

    def test_raw_usage_output_tokens_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawUsage(output_tokens=-1)

    # ------------------------------------------------------------------
    # dataset identity strings strict non-empty / SHA format
    # ------------------------------------------------------------------

    def test_dataset_id_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", dataset_id="")

    def test_dataset_id_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", dataset_id="   ")

    def test_dataset_schema_version_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", dataset_schema_version="")

    def test_dataset_content_sha256_short_rejected(self) -> None:
        """``"abc123"`` MUST be rejected (not 64 chars)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", dataset_content_sha256="abc123")

    def test_dataset_content_sha256_uppercase_rejected(self) -> None:
        """Uppercase hex MUST be rejected (lowercase only)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", dataset_content_sha256="A" * 64)

    def test_dataset_content_sha256_non_hex_rejected(self) -> None:
        """Non-hex chars MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", dataset_content_sha256="g" * 64)

    def test_dataset_content_sha256_too_short_rejected(self) -> None:
        """63 chars MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", dataset_content_sha256="a" * 63)

    def test_dataset_content_sha256_too_long_rejected(self) -> None:
        """65 chars MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", dataset_content_sha256="a" * 65)

    # ------------------------------------------------------------------
    # Non-regression: valid artifact still constructs
    # ------------------------------------------------------------------

    def test_valid_artifact_does_not_regress(self) -> None:
        """A schema-valid RawArtifact MUST construct without error.

        This is the non-regression guard: the strict validators must
        not reject well-formed inputs.
        """
        artifact = _make_valid_artifact()
        assert artifact.case_id == "case-a"
        assert artifact.run_id == "phase1-test"
        assert artifact.run_index == 0
        assert artifact.budget_exhausted is False
        assert artifact.thinking_enabled is False
        assert artifact.dataset_content_sha256 == _VALID_SHA

    def test_valid_artifact_with_none_identity_does_not_regress(self) -> None:
        """A RawArtifact with ``dataset_content_sha256=None`` MUST construct.

        ``None`` is allowed for backwards compat with pre-P0-2 artifacts.
        """
        artifact = RawArtifact(
            case_id="c",
            run_id="r",
            dataset_id=None,
            dataset_schema_version=None,
            dataset_content_sha256=None,
        )
        assert artifact.dataset_content_sha256 is None


# ===========================================================================
# SECTION 2: Artifact load audit (P0-1)
#
# load_artifacts_with_audit MUST produce typed counts for each failure
# mode. Corrupt/invalid/foreign artifacts MUST NOT silently disappear.
# ===========================================================================


class TestArtifactLoadAudit:
    """P0-1: load_artifacts_with_audit counts every failure mode."""

    def test_corrupt_json_counted_as_invalid_json(self, tmp_path: Path) -> None:
        """A file with invalid JSON syntax → invalid_json_count=1."""
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(artifact_dir, "bad.json", "{not valid json}")
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.discovered_file_count == 1
        assert result.invalid_json_count == 1
        assert result.invalid_schema_count == 0
        assert result.foreign_run_id_count == 0
        assert result.invalid_artifact_count == 1
        assert result.is_clean is False
        assert len(result.valid_artifacts) == 0

    def test_non_object_json_array_counted_as_invalid_json(
        self, tmp_path: Path
    ) -> None:
        """A JSON array (non-object) → invalid_json_count=1."""
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(artifact_dir, "array.json", [1, 2, 3])
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.invalid_json_count == 1
        assert result.invalid_schema_count == 0
        assert result.is_clean is False

    def test_non_object_json_string_counted_as_invalid_json(
        self, tmp_path: Path
    ) -> None:
        """A JSON string (non-object) → invalid_json_count=1."""
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(artifact_dir, "str.json", '"hello"')
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.invalid_json_count == 1
        assert result.invalid_schema_count == 0

    def test_schema_invalid_artifact_counted_as_invalid_schema(
        self, tmp_path: Path
    ) -> None:
        """A JSON object that fails RawArtifact strict validation →
        invalid_schema_count=1.
        """
        artifact_dir = tmp_path / "artifacts"
        # run_index=True would have been coerced to 1 by lenient Pydantic;
        # the strict schema rejects it.
        _write_artifact_json(
            artifact_dir,
            "bad_schema.json",
            {
                "case_id": "case-a",
                "run_id": "phase1-test",
                "run_index": True,  # ← strict int rejects this
            },
        )
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.invalid_json_count == 0
        assert result.invalid_schema_count == 1
        assert result.invalid_artifact_count == 1
        assert result.is_clean is False
        assert len(result.valid_artifacts) == 0

    def test_foreign_run_id_counted_separately(self, tmp_path: Path) -> None:
        """An artifact with a different run_id → foreign_run_id_count=1."""
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(
            artifact_dir,
            "foreign.json",
            {
                "case_id": "case-a",
                "run_id": "other-run",  # ← foreign
                "run_index": 0,
            },
        )
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.invalid_json_count == 0
        assert result.invalid_schema_count == 0
        assert result.foreign_run_id_count == 1
        assert result.invalid_artifact_count == 1
        assert result.is_clean is False
        assert len(result.valid_artifacts) == 0

    def test_mixed_valid_and_invalid_counts_correct(self, tmp_path: Path) -> None:
        """A directory with valid + invalid + foreign files → all counts
        correct, valid artifact appears in valid_artifacts.
        """
        artifact_dir = tmp_path / "artifacts"
        # Valid artifact
        valid = _make_valid_artifact()
        _write_artifact_json(
            artifact_dir, "valid.json", json.loads(valid.model_dump_json())
        )
        # Corrupt JSON
        _write_artifact_json(artifact_dir, "corrupt.json", "{bad}")
        # Schema invalid (run_index=True)
        _write_artifact_json(
            artifact_dir,
            "bad_schema.json",
            {
                "case_id": "case-a",
                "run_id": "phase1-test",
                "run_index": True,
            },
        )
        # Foreign run_id
        _write_artifact_json(
            artifact_dir,
            "foreign.json",
            {
                "case_id": "case-a",
                "run_id": "other-run",
                "run_index": 0,
            },
        )
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.discovered_file_count == 4
        assert result.invalid_json_count == 1
        assert result.invalid_schema_count == 1
        assert result.foreign_run_id_count == 1
        assert result.invalid_artifact_count == 3
        assert result.is_clean is False
        assert len(result.valid_artifacts) == 1
        assert result.valid_artifacts[0].case_id == "case-a"

    def test_invalid_result_does_not_carry_sensitive_data(
        self, tmp_path: Path
    ) -> None:
        """ArtifactLoadResult MUST NOT carry exception text, JSON content,
        or file paths — only typed counts.
        """
        artifact_dir = tmp_path / "artifacts"
        # Write a file with potentially sensitive content.
        _write_artifact_json(
            artifact_dir,
            "secret.json",
            "{bad json with api_key=sk-xxxxx and /home/user/secret/path}",
        )
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        # The result is a frozen dataclass — inspect its repr.
        result_repr = repr(result)
        assert "sk-xxxxx" not in result_repr
        assert "/home/user/secret" not in result_repr
        assert "api_key" not in result_repr
        # Only typed counts are present.
        assert result.discovered_file_count == 1
        assert result.invalid_json_count == 1

    def test_absent_manifest_plus_corrupt_artifact_yields_incomplete(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: absent manifest + 1 corrupt artifact file →
        ``blocked_incomplete_real_model_run`` (NOT blocked_by_real_model_run).
        """
        cases = [_make_case()]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)
        runs_dir = tmp_path / "runs"
        # Write a corrupt artifact file (no manifest).
        artifact_dir = runs_dir / "phase1-test" / "artifacts"
        _write_artifact_json(artifact_dir, "corrupt.json", "{bad json}")
        report_output = tmp_path / "report.md"

        rc = _RUNNER.aggregate(
            run_id="phase1-test",
            runs_dir=runs_dir,
            dataset_dir=dataset_dir,
            report_output=report_output,
        )
        assert rc == 0
        verdict = _read_verdict_from_report(report_output)
        assert verdict == "blocked_incomplete_real_model_run", (
            "Absent manifest + corrupt artifact MUST yield "
            f"blocked_incomplete_real_model_run (got {verdict!r})"
        )

    def test_absent_manifest_plus_foreign_artifact_yields_incomplete(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: absent manifest + 1 foreign-run_id artifact →
        ``blocked_incomplete_real_model_run``.
        """
        cases = [_make_case()]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)
        runs_dir = tmp_path / "runs"
        artifact_dir = runs_dir / "phase1-test" / "artifacts"
        # Foreign run_id — the artifact is valid but belongs to a different run.
        foreign = RawArtifact(
            case_id="case-a",
            run_id="other-run",  # ← foreign
            run_index=0,
        )
        _write_artifact_json(
            artifact_dir,
            "foreign.json",
            json.loads(foreign.model_dump_json()),
        )
        report_output = tmp_path / "report.md"

        rc = _RUNNER.aggregate(
            run_id="phase1-test",
            runs_dir=runs_dir,
            dataset_dir=dataset_dir,
            report_output=report_output,
        )
        assert rc == 0
        verdict = _read_verdict_from_report(report_output)
        assert verdict == "blocked_incomplete_real_model_run", (
            "Absent manifest + foreign artifact MUST yield "
            f"blocked_incomplete_real_model_run (got {verdict!r})"
        )

    def test_absent_manifest_plus_zero_files_yields_blocked_by_real_model_run(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: absent manifest + 0 artifact files →
        ``blocked_by_real_model_run`` (the "never ran" verdict).
        """
        cases = [_make_case()]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)
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
        verdict = _read_verdict_from_report(report_output)
        assert verdict == "blocked_by_real_model_run", (
            "Absent manifest + zero files MUST yield "
            f"blocked_by_real_model_run (got {verdict!r})"
        )

    def test_empty_directory_yields_clean_result(self, tmp_path: Path) -> None:
        """An existing but empty artifact directory → all counts 0,
        is_clean=True.
        """
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.discovered_file_count == 0
        assert result.invalid_json_count == 0
        assert result.invalid_schema_count == 0
        assert result.foreign_run_id_count == 0
        assert result.invalid_artifact_count == 0
        assert result.is_clean is True
        assert len(result.valid_artifacts) == 0

    def test_nonexistent_directory_yields_clean_result(
        self, tmp_path: Path
    ) -> None:
        """A non-existent artifact directory → all counts 0, is_clean=True."""
        artifact_dir = tmp_path / "does-not-exist"
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.discovered_file_count == 0
        assert result.invalid_artifact_count == 0
        assert result.is_clean is True


# ===========================================================================
# SECTION 3: Coverage / evaluation readiness (P0-2)
#
# AggregateReadinessAudit is the single source of truth for normal-verdict
# readiness. _decide_normal_verdict([]) MUST NOT return accepted. Unknown
# cases MUST force blocked_incomplete_real_model_run.
# ===========================================================================


class TestEvaluationReadiness:
    """P0-2: AggregateReadinessAudit + _decide_normal_verdict + _decide_final_verdict."""

    # ------------------------------------------------------------------
    # run_index=true cannot satisfy planned index=1 (schema-level)
    # ------------------------------------------------------------------

    def test_run_index_true_cannot_satisfy_planned_index(self) -> None:
        """``run_index=True`` is rejected by the strict schema, so it
        can never produce a RawArtifact that satisfies a manifest's
        ``planned_run_index=1``.

        This is the P0-1 repro 1 regression guard: previously Pydantic
        coerced ``True`` → ``1``, which then matched the manifest's
        planned run_index=1 and bypassed the coverage audit.
        """
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", run_index=True)  # type: ignore[arg-type]
        # The strict schema makes it impossible to construct such an
        # artifact, so the coverage audit can never be bypassed this way.

    # ------------------------------------------------------------------
    # _decide_normal_verdict([]) MUST NOT return accepted
    # ------------------------------------------------------------------

    def test_empty_case_results_not_accepted(self) -> None:
        """``_decide_normal_verdict([])`` MUST NOT return ``accepted``.

        This is the structural fix for the ``all([]) → True`` bug.
        Previously, an empty case_results list (produced by the
        ``warn + continue`` unknown-case path) returned ``accepted``
        because ``all([])`` is ``True`` in Python.
        """
        verdict, allow_a4, allow_b1 = _RUNNER._decide_normal_verdict([])
        assert verdict != "accepted", (
            "Empty case_results MUST NEVER yield accepted — "
            "this is the all([]) → True bug."
        )
        assert allow_a4 is False
        assert allow_b1 is False

    def test_empty_case_results_with_readiness_not_accepted(self) -> None:
        """``_decide_normal_verdict([], readiness=...)`` MUST NOT return
        ``accepted`` even when readiness is provided.
        """
        readiness = _make_readiness(evaluated_case_result_count=0)
        verdict, _, _ = _RUNNER._decide_normal_verdict(
            [], readiness=readiness
        )
        assert verdict != "accepted"

    def test_normal_verdict_with_unready_readiness_blocked(self) -> None:
        """When ``readiness.ready_for_normal_verdict=False``, the normal
        verdict path MUST be blocked even if case_results is non-empty.
        """
        readiness = _make_readiness(
            artifact_load_clean=False,  # ← makes ready_for_normal_verdict False
            evaluated_case_result_count=1,
        )
        case_results = [_make_passing_case_result()]
        verdict, allow_a4, allow_b1 = _RUNNER._decide_normal_verdict(
            case_results, readiness=readiness
        )
        assert verdict == "blocked_incomplete_real_model_run"
        assert allow_a4 is False
        assert allow_b1 is False

    # ------------------------------------------------------------------
    # Evaluated count mismatch → blocked_incomplete_real_model_run
    # ------------------------------------------------------------------

    def test_evaluated_count_less_than_planned_yields_incomplete(self) -> None:
        """``evaluated_case_result_count < planned_count`` →
        ``blocked_incomplete_real_model_run`` via precedence 9.
        """
        readiness = _make_readiness(
            planned_count=2,
            evaluable_artifact_count=2,
            evaluated_case_result_count=1,  # ← less than planned
        )
        coverage = _make_coverage_audit(
            planned_count=2, evaluable_artifact_count=2
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[_make_passing_case_result()],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=2,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    def test_evaluated_count_greater_than_planned_yields_incomplete(
        self
    ) -> None:
        """``evaluated_case_result_count > planned_count`` →
        ``blocked_incomplete_real_model_run`` via precedence 9.
        """
        readiness = _make_readiness(
            planned_count=1,
            evaluable_artifact_count=1,
            evaluated_case_result_count=2,  # ← greater than planned
        )
        coverage = _make_coverage_audit(
            planned_count=1, evaluable_artifact_count=1
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[
                _make_passing_case_result(),
                _make_passing_case_result(case_id="case-b"),
            ],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=2,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    def test_evaluated_count_equals_planned_all_pass_yields_accepted(
        self
    ) -> None:
        """``evaluated_case_result_count == planned_count > 0`` and all
        dimensions pass → ``accepted``.
        """
        readiness = _make_readiness(
            planned_count=1,
            evaluable_artifact_count=1,
            evaluated_case_result_count=1,
        )
        coverage = _make_coverage_audit(
            planned_count=1, evaluable_artifact_count=1
        )
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[_make_passing_case_result()],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
            readiness=readiness,
        )
        assert verdict == "accepted"
        assert allow_a4 is True
        assert allow_b1 is True

    def test_evaluated_count_equals_planned_quality_fail_yields_rework(
        self
    ) -> None:
        """``evaluated_case_result_count == planned_count > 0`` but a
        high-severity dimension fails → ``rework``.
        """
        readiness = _make_readiness(
            planned_count=1,
            evaluable_artifact_count=1,
            evaluated_case_result_count=1,
        )
        coverage = _make_coverage_audit(
            planned_count=1, evaluable_artifact_count=1
        )
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[_make_failing_case_result()],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
            readiness=readiness,
        )
        assert verdict == "rework"
        assert allow_a4 is True
        assert allow_b1 is False

    # ------------------------------------------------------------------
    # Unknown dataset case binding (P0-2)
    # ------------------------------------------------------------------

    def test_unknown_planned_case_yields_incomplete(self) -> None:
        """Manifest planned a case_id not in the dataset →
        ``blocked_incomplete_real_model_run`` via precedence 8.
        """
        readiness = _make_readiness(
            planned_count=1,
            evaluable_artifact_count=1,
            unknown_planned_case_count=1,  # ← unknown planned case
            evaluated_case_result_count=1,
        )
        coverage = _make_coverage_audit(
            planned_count=1, evaluable_artifact_count=1
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[_make_passing_case_result()],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    def test_unknown_artifact_case_yields_incomplete(self) -> None:
        """An artifact references a case_id not in the dataset →
        ``blocked_incomplete_real_model_run`` via precedence 8.
        """
        readiness = _make_readiness(
            planned_count=1,
            evaluable_artifact_count=1,
            unknown_artifact_case_count=1,  # ← unknown artifact case
            evaluated_case_result_count=1,
        )
        coverage = _make_coverage_audit(
            planned_count=1, evaluable_artifact_count=1
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[_make_passing_case_result()],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    def test_valid_plus_unknown_mixed_yields_incomplete(self) -> None:
        """1 valid case + 1 unknown case → whole run MUST be blocked."""
        readiness = _make_readiness(
            planned_count=2,
            evaluable_artifact_count=2,
            unknown_planned_case_count=1,  # ← 1 of 2 is unknown
            evaluated_case_result_count=2,
        )
        coverage = _make_coverage_audit(
            planned_count=2, evaluable_artifact_count=2
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[
                _make_passing_case_result(),
                _make_passing_case_result(case_id="case-b"),
            ],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=2,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    # ------------------------------------------------------------------
    # End-to-end aggregate: unknown artifact case
    # ------------------------------------------------------------------

    def test_aggregate_unknown_artifact_case_yields_incomplete(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: an artifact with a case_id NOT in the dataset →
        ``blocked_incomplete_real_model_run`` (NOT accepted).

        This is the P0-2 repro 2 regression guard: previously the
        ``warn + continue`` path let unknown-case artifacts be silently
        skipped, producing ``case_results=[]`` and the
        ``all([]) → accepted`` bug.
        """
        from claread_eval.reader_record_ask.loader import (
            load_r4_a3_dataset_with_snapshot,
        )

        cases = [_make_case(case_id="case-a")]
        dataset_dir = tmp_path / "dataset"
        _write_dataset_dir(dataset_dir, cases=cases)
        snapshot = load_r4_a3_dataset_with_snapshot(dataset_dir)
        identity = snapshot.identity

        # Artifact references case-b which doesn't exist in the dataset.
        artifact = _make_valid_artifact(
            case_id="case-b",  # ← NOT in dataset
            dataset_id=identity.dataset_id,
            dataset_schema_version=identity.schema_version,
            dataset_content_sha256=identity.content_sha256,
        )
        runs_dir = tmp_path / "runs"
        _write_completed_manifest(
            runs_dir,
            run_id="phase1-test",
            planned={"case-b": [0]},  # manifest also plans the unknown case
            dataset_id=identity.dataset_id,
            dataset_schema_version=identity.schema_version,
            dataset_content_sha256=identity.content_sha256,
        )
        # Write the artifact file.
        artifact_dir = runs_dir / "phase1-test" / "artifacts"
        _write_artifact_json(
            artifact_dir,
            "case-b.json",
            json.loads(artifact.model_dump_json()),
        )
        report_output = tmp_path / "report.md"

        rc = _RUNNER.aggregate(
            run_id="phase1-test",
            runs_dir=runs_dir,
            dataset_dir=dataset_dir,
            report_output=report_output,
        )
        assert rc == 0
        verdict = _read_verdict_from_report(report_output)
        assert verdict == "blocked_incomplete_real_model_run", (
            "Unknown artifact case MUST yield blocked_incomplete_real_model_run "
            f"(got {verdict!r}) — this is the all([]) → accepted bug."
        )

    # ------------------------------------------------------------------
    # Precedence non-regression
    # ------------------------------------------------------------------

    def test_identity_mismatch_precedence_does_not_regress(self) -> None:
        """Identity mismatch still wins over all other blockers."""
        readiness = _make_readiness(
            artifact_load_clean=False,
            unknown_planned_case_count=1,
            evaluated_case_result_count=0,
        )
        coverage = _make_coverage_audit(
            manifest_present=False,
            manifest_status=None,
            planned_count=0,
            evaluable_artifact_count=0,
            dataset_identity=None,
        )
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=coverage,
            identity_mismatched_count=1,  # ← identity mismatch
            real_model_blocked=True,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
            readiness=readiness,
        )
        assert verdict == "blocked_dataset_identity_mismatch"
        assert allow_a4 is False
        assert allow_b1 is False

    def test_corrupt_manifest_precedence_does_not_regress(self) -> None:
        """Corrupt manifest still yields blocked_incomplete_real_model_run
        (NOT blocked_by_real_model_run).
        """
        readiness = _make_readiness(
            manifest_state="corrupt",
            manifest_present=False,
            manifest_run_id_matches=None,
            manifest_status=None,
            manifest_is_complete=False,
            planned_count=0,
            evaluable_artifact_count=0,
            evaluated_case_result_count=0,
        )
        coverage = _make_coverage_audit(
            manifest_present=False,
            manifest_status=None,
            manifest_state="corrupt",
            planned_count=0,
            evaluable_artifact_count=0,
            dataset_identity=None,
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=True,
            has_budget_exhausted=False,
            total_artifacts_loaded=0,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    def test_foreign_manifest_precedence_does_not_regress(self) -> None:
        """Foreign manifest (valid but run_id mismatch) yields
        blocked_incomplete_real_model_run.
        """
        readiness = _make_readiness(
            manifest_state="valid",
            manifest_present=True,
            manifest_run_id_matches=False,  # ← foreign
            manifest_status="completed",
            planned_count=1,
            evaluable_artifact_count=1,
            evaluated_case_result_count=1,
        )
        coverage = _make_coverage_audit(
            manifest_present=True,
            manifest_status="completed",
            manifest_state="valid",
            manifest_run_id_matches=False,  # ← foreign
            planned_count=1,
            evaluable_artifact_count=1,
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[_make_passing_case_result()],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    def test_budget_exhausted_precedence_does_not_regress(self) -> None:
        """``manifest_status="budget_exhausted"`` still yields
        blocked_incomplete_real_model_run.
        """
        readiness = _make_readiness(
            manifest_status="budget_exhausted",
            manifest_is_complete=False,
            planned_count=2,
            evaluable_artifact_count=1,
            evaluated_case_result_count=1,
        )
        coverage = _make_coverage_audit(
            manifest_present=True,
            manifest_status="budget_exhausted",
            planned_count=2,
            evaluable_artifact_count=1,
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[_make_passing_case_result()],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=True,
            total_artifacts_loaded=1,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    def test_artifact_load_invalid_precedence(self) -> None:
        """Invalid/corrupt/foreign artifacts → blocked_incomplete_real_model_run
        via precedence 5 (before budget / coverage / unknown cases).
        """
        readiness = _make_readiness(
            artifact_load_clean=False,  # ← artifact load not clean
            invalid_artifact_count=1,
            discovered_file_count=1,
            planned_count=1,
            evaluable_artifact_count=0,
            evaluated_case_result_count=0,
        )
        coverage = _make_coverage_audit(
            manifest_present=True,
            manifest_status="completed",
            planned_count=1,
            evaluable_artifact_count=0,
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=True,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    def test_normal_completed_full_coverage_does_not_regress(self) -> None:
        """Completed manifest + full coverage + all pass → accepted.

        Non-regression: the strict validators and readiness audit must
        NOT break the happy path.
        """
        readiness = _make_readiness(
            planned_count=2,
            evaluable_artifact_count=2,
            evaluated_case_result_count=2,
        )
        coverage = _make_coverage_audit(
            planned_count=2,
            evaluable_artifact_count=2,
        )
        verdict, allow_a4, allow_b1 = _RUNNER._decide_final_verdict(
            case_results=[
                _make_passing_case_result(),
                _make_passing_case_result(case_id="case-b"),
            ],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=False,
            has_budget_exhausted=False,
            total_artifacts_loaded=2,
            readiness=readiness,
        )
        assert verdict == "accepted"
        assert allow_a4 is True
        assert allow_b1 is True

    # ------------------------------------------------------------------
    # AggregateReadinessAudit property tests
    # ------------------------------------------------------------------

    def test_ready_for_normal_verdict_all_conditions_true(self) -> None:
        """When all conditions hold, ready_for_normal_verdict is True."""
        readiness = _make_readiness(
            planned_count=1,
            evaluable_artifact_count=1,
            evaluated_case_result_count=1,
        )
        assert readiness.ready_for_normal_verdict is True

    def test_ready_for_normal_verdict_false_when_evaluated_zero(self) -> None:
        """When evaluated_case_result_count=0, ready_for_normal_verdict
        is False (even if planned_count=0 — the > 0 check fails).
        """
        readiness = _make_readiness(
            planned_count=0,
            evaluable_artifact_count=0,
            evaluated_case_result_count=0,
        )
        assert readiness.ready_for_normal_verdict is False

    def test_pre_evaluator_ready_true_when_preconditions_hold(self) -> None:
        """pre_evaluator_ready is True when all pre-evaluator conditions
        hold (doesn't require evaluated_case_result_count).
        """
        readiness = _make_readiness(
            planned_count=1,
            evaluable_artifact_count=1,
            evaluated_case_result_count=0,  # ← not yet evaluated
        )
        assert readiness.pre_evaluator_ready is True
        # But ready_for_normal_verdict is False (count is 0).
        assert readiness.ready_for_normal_verdict is False

    def test_pre_evaluator_ready_false_when_unknown_case_present(self) -> None:
        """pre_evaluator_ready is False when unknown_artifact_case_count > 0
        — the evaluator MUST NOT run.
        """
        readiness = _make_readiness(
            planned_count=1,
            evaluable_artifact_count=1,
            unknown_artifact_case_count=1,
            evaluated_case_result_count=0,
        )
        assert readiness.pre_evaluator_ready is False

    def test_pre_evaluator_ready_false_when_artifact_load_not_clean(
        self
    ) -> None:
        """pre_evaluator_ready is False when artifact_load_clean is False
        — the evaluator MUST NOT run on corrupt artifacts.
        """
        readiness = _make_readiness(
            artifact_load_clean=False,
            planned_count=1,
            evaluable_artifact_count=1,
            evaluated_case_result_count=0,
        )
        assert readiness.pre_evaluator_ready is False


# ===========================================================================
# Helper: extract verdict from report
# ===========================================================================


def _read_verdict_from_report(report_path: Path) -> str:
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
    pytest.fail(
        f"report missing verdict line; first 500 chars: {text[:500]!r}"
    )


# ===========================================================================
# SECTION 4: P0-2 Evaluator-scored structural fields strict schema
#
# These fields directly drive evaluator verdicts (tool_decision,
# evidence_minimality, usage_observability, answer_success). Coercion
# here would let a corrupted JSON file masquerade as a valid artifact
# and silently flip a ``passed=False`` to ``passed=True``.
# ===========================================================================


class TestRawArtifactEvaluatorScoredStrictSchema:
    """P0-2: Evaluator-scored structural fields reject Pydantic coercion.

    These are NOT display fields — they directly drive evaluator
    verdicts. See the Evaluator-consumed Field Matrix in the delivery
    report for the full mapping.
    """

    # ------------------------------------------------------------------
    # read_range_calls strict non-negative int (reject bool / str / float / neg)
    # ------------------------------------------------------------------

    def test_read_range_calls_true_rejected(self) -> None:
        """``read_range_calls=True`` MUST be rejected.

        Previously Pydantic coerced ``True`` → ``1``, which would
        satisfy the ``tool_decision`` evaluator's ``rr > 0`` branch
        and bypass a ``required``-policy failure.
        """
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", read_range_calls=True)  # type: ignore[arg-type]

    def test_read_range_calls_string_rejected(self) -> None:
        """``read_range_calls="1"`` MUST be rejected (would coerce to 1)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", read_range_calls="1")  # type: ignore[arg-type]

    def test_read_range_calls_float_rejected(self) -> None:
        """``read_range_calls=1.0`` MUST be rejected (would coerce to 1)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", read_range_calls=1.0)  # type: ignore[arg-type]

    def test_read_range_calls_negative_rejected(self) -> None:
        """``read_range_calls=-1`` MUST be rejected (negative count)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", read_range_calls=-1)

    # ------------------------------------------------------------------
    # search_current_article_calls strict non-negative int
    # ------------------------------------------------------------------

    def test_search_current_article_calls_true_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c", run_id="r", search_current_article_calls=True  # type: ignore[arg-type]
            )

    def test_search_current_article_calls_string_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c", run_id="r", search_current_article_calls="1"  # type: ignore[arg-type]
            )

    def test_search_current_article_calls_float_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c", run_id="r", search_current_article_calls=1.0  # type: ignore[arg-type]
            )

    def test_search_current_article_calls_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c", run_id="r", search_current_article_calls=-1
            )

    # ------------------------------------------------------------------
    # baseline_is_complete strict bool | None (reject str / int)
    # ------------------------------------------------------------------

    def test_baseline_is_complete_string_true_rejected(self) -> None:
        """``baseline_is_complete="true"`` MUST be rejected.

        Previously Pydantic coerced ``"true"`` → ``True``, which would
        flip the ``evidence_minimality`` soft-failure check
        (``if artifact.baseline_is_complete is True``).
        """
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_is_complete="true")  # type: ignore[arg-type]

    def test_baseline_is_complete_string_false_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_is_complete="false")  # type: ignore[arg-type]

    def test_baseline_is_complete_int_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_is_complete=0)  # type: ignore[arg-type]

    def test_baseline_is_complete_int_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_is_complete=1)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # baseline_is_injected strict bool | None
    # ------------------------------------------------------------------

    def test_baseline_is_injected_string_true_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_is_injected="true")  # type: ignore[arg-type]

    def test_baseline_is_injected_string_false_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_is_injected="false")  # type: ignore[arg-type]

    def test_baseline_is_injected_int_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_is_injected=0)  # type: ignore[arg-type]

    def test_baseline_is_injected_int_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_is_injected=1)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # latency_seconds: reject bool / str / NaN / Infinity / negative
    # ------------------------------------------------------------------

    def test_latency_seconds_bool_rejected(self) -> None:
        """``latency_seconds=True`` MUST be rejected.

        Previously Pydantic coerced ``True`` → ``1.0``, which would
        satisfy the ``usage_observability`` ``latency_seconds > 0`` check.
        """
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", latency_seconds=True)  # type: ignore[arg-type]

    def test_latency_seconds_string_rejected(self) -> None:
        """``latency_seconds="1"`` MUST be rejected (would coerce to 1.0)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", latency_seconds="1")  # type: ignore[arg-type]

    def test_latency_seconds_negative_rejected(self) -> None:
        """``latency_seconds=-1.5`` MUST be rejected (negative)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", latency_seconds=-1.5)

    def test_latency_seconds_nan_rejected(self) -> None:
        """``latency_seconds=NaN`` MUST be rejected (not finite)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", latency_seconds=float("nan"))

    def test_latency_seconds_infinity_rejected(self) -> None:
        """``latency_seconds=Infinity`` MUST be rejected (not finite)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", latency_seconds=float("inf"))

    def test_latency_seconds_negative_infinity_rejected(self) -> None:
        """``latency_seconds=-Infinity`` MUST be rejected."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", latency_seconds=float("-inf"))

    def test_latency_seconds_int_allowed_and_normalized(self) -> None:
        """Design choice: JSON integer ``1`` is ALLOWED and normalized to
        ``1.0``. A whole-second latency is a legitimate representation.
        """
        artifact = RawArtifact(case_id="c", run_id="r", latency_seconds=1)
        assert artifact.latency_seconds == 1.0
        assert isinstance(artifact.latency_seconds, float)

    def test_latency_seconds_none_allowed(self) -> None:
        """``latency_seconds=None`` is allowed (means not recorded)."""
        artifact = RawArtifact(case_id="c", run_id="r", latency_seconds=None)
        assert artifact.latency_seconds is None

    def test_latency_seconds_valid_float_allowed(self) -> None:
        """A valid positive float is accepted unchanged."""
        artifact = RawArtifact(case_id="c", run_id="r", latency_seconds=1.5)
        assert artifact.latency_seconds == 1.5

    def test_latency_seconds_zero_allowed(self) -> None:
        """``latency_seconds=0.0`` is allowed (boundary — non-negative).

        Note: ``usage_observability`` will flag ``<= 0`` as a failure,
        but the SCHEMA accepts it — a zero latency is a quality issue
        for the evaluator, not a schema corruption.
        """
        artifact = RawArtifact(case_id="c", run_id="r", latency_seconds=0.0)
        assert artifact.latency_seconds == 0.0

    # ------------------------------------------------------------------
    # finalized_status: Literal enum (reject typos / non-allowed values)
    # ------------------------------------------------------------------

    def test_finalized_status_typo_trailing_space_rejected(self) -> None:
        """``"ok "`` (trailing space) MUST be rejected.

        Previously ``str | None`` accepted it, and the
        ``answer_success`` evaluator's ``finalized_status != "ok"``
        check would incorrectly flag a valid artifact.
        """
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", finalized_status="ok ")

    def test_finalized_status_typo_uppercase_rejected(self) -> None:
        """``"OK"`` MUST be rejected (case-sensitive Literal)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", finalized_status="OK")

    def test_finalized_status_non_allowed_value_rejected(self) -> None:
        """``"completed"`` MUST be rejected (not in the Literal set)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", finalized_status="completed")

    def test_finalized_status_int_rejected(self) -> None:
        """``1`` MUST be rejected (Literal does not coerce int)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", finalized_status=1)  # type: ignore[arg-type]

    def test_finalized_status_valid_literals_pass(self) -> None:
        """All four production FinalizeStatus values MUST be accepted."""
        for status in ("ok", "context_stale", "invalid_citations", "unavailable"):
            artifact = RawArtifact(
                case_id="c", run_id="r", finalized_status=status  # type: ignore[arg-type]
            )
            assert artifact.finalized_status == status

    def test_finalized_status_none_allowed(self) -> None:
        artifact = RawArtifact(case_id="c", run_id="r", finalized_status=None)
        assert artifact.finalized_status is None

    # ------------------------------------------------------------------
    # response_kind: Literal enum
    # ------------------------------------------------------------------

    def test_response_kind_typo_plural_rejected(self) -> None:
        """``"grounded_answers"`` MUST be rejected (typo)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", response_kind="grounded_answers")

    def test_response_kind_non_allowed_value_rejected(self) -> None:
        """``"answer"`` MUST be rejected (not in the Literal set)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", response_kind="answer")

    def test_response_kind_int_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", response_kind=1)  # type: ignore[arg-type]

    def test_response_kind_valid_literals_pass(self) -> None:
        for kind in ("grounded_answer", "clarification", "unavailable"):
            artifact = RawArtifact(
                case_id="c", run_id="r", response_kind=kind  # type: ignore[arg-type]
            )
            assert artifact.response_kind == kind

    # ------------------------------------------------------------------
    # baseline_status: Literal enum
    # ------------------------------------------------------------------

    def test_baseline_status_typo_trailing_space_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_status="injected ")

    def test_baseline_status_non_allowed_value_rejected(self) -> None:
        """``"done"`` MUST be rejected (not in the Literal set)."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", baseline_status="done")

    def test_baseline_status_valid_literals_pass(self) -> None:
        for status in (
            "injected",
            "document_scope_unavailable",
            "envelope_mismatch",
            "no_units",
        ):
            artifact = RawArtifact(
                case_id="c", run_id="r", baseline_status=status  # type: ignore[arg-type]
            )
            assert artifact.baseline_status == status

    # ------------------------------------------------------------------
    # cited_evidence_handles: list[StrictStr] + non-empty validator
    # ------------------------------------------------------------------

    def test_cited_handles_bool_element_rejected(self) -> None:
        """A bool element MUST be rejected (StrictStr)."""
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c", run_id="r", cited_evidence_handles=[True]  # type: ignore[list-item]
            )

    def test_cited_handles_int_element_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c", run_id="r", cited_evidence_handles=[123]  # type: ignore[list-item]
            )

    def test_cited_handles_empty_string_element_rejected(self) -> None:
        """An empty string handle MUST be rejected.

        ``""`` would corrupt the ``evidence_minimality`` set-membership
        check (``{ev.handle_id for ev in ...}`` would include ``""``).
        """
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", cited_evidence_handles=[""])

    def test_cited_handles_whitespace_string_element_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c", run_id="r", cited_evidence_handles=["   "]
            )

    def test_cited_handles_valid_list_passes(self) -> None:
        """A list of non-empty strings is accepted."""
        artifact = RawArtifact(
            case_id="c", run_id="r", cited_evidence_handles=["h1", "h2"]
        )
        assert artifact.cited_evidence_handles == ["h1", "h2"]

    def test_cited_handles_empty_list_allowed(self) -> None:
        """An empty list is allowed (no citations — valid)."""
        artifact = RawArtifact(
            case_id="c", run_id="r", cited_evidence_handles=[]
        )
        assert artifact.cited_evidence_handles == []

    def test_cited_handles_duplicate_preserved_for_evaluator(self) -> None:
        """Duplicate handles MUST be preserved by the schema.

        Duplicate detection is the ``evidence_minimality`` evaluator's
        responsibility — rejecting duplicates here would swallow a
        content-quality issue that the evaluator is designed to catch.
        """
        artifact = RawArtifact(
            case_id="c", run_id="r", cited_evidence_handles=["h1", "h1"]
        )
        # Schema accepts; evaluator will fail.
        assert artifact.cited_evidence_handles == ["h1", "h1"]

    def test_cited_handles_unknown_preserved_for_evaluator(self) -> None:
        """Unknown handles (not in observations) MUST be preserved.

        Unknown-handle detection is the ``evidence_minimality``
        evaluator's responsibility.
        """
        artifact = RawArtifact(
            case_id="c", run_id="r", cited_evidence_handles=["unknown_handle"]
        )
        assert artifact.cited_evidence_handles == ["unknown_handle"]


# ===========================================================================
# SECTION 5: P0-2 RawEvidenceObservation strict schema
# ===========================================================================


class TestRawEvidenceObservationStrictSchema:
    """P0-2: RawEvidenceObservation fields reject coercion / typos.

    The ``evidence_minimality`` evaluator reads ``handle_id`` (set
    membership), ``kind`` (``== "search_hit"``), and ``snippet``
    (content). A typo'd kind would silently bypass the soft-failure
    check; an empty handle would corrupt the set-membership check.
    """

    from claread_eval.reader_record_ask.evaluators.artifact import (
        RawEvidenceObservation as _Obs,
    )

    # ------------------------------------------------------------------
    # handle_id: StrictStr + non-empty/non-whitespace
    # ------------------------------------------------------------------

    def test_handle_id_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._Obs(handle_id="", kind="article_seed", provenance="baseline_context")

    def test_handle_id_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="   ", kind="article_seed", provenance="baseline_context"
            )

    def test_handle_id_bool_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id=True,  # type: ignore[arg-type]
                kind="article_seed",
                provenance="baseline_context",
            )

    def test_handle_id_int_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id=123,  # type: ignore[arg-type]
                kind="article_seed",
                provenance="baseline_context",
            )

    # ------------------------------------------------------------------
    # kind: Literal enum (reject typos)
    # ------------------------------------------------------------------

    def test_kind_typo_plural_rejected(self) -> None:
        """``"search_hits"`` MUST be rejected (typo).

        Previously ``str`` accepted it, and the ``evidence_minimality``
        evaluator's ``all(k == "search_hit" for k in kinds)`` check
        would return ``False`` — silently bypassing the soft-failure.
        """
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="h1", kind="search_hits", provenance="search_current_article"
            )

    def test_kind_typo_short_rejected(self) -> None:
        """``"search"`` MUST be rejected (not in Literal set)."""
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="h1", kind="search", provenance="search_current_article"
            )

    def test_kind_non_allowed_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="h1", kind="unknown_kind", provenance="baseline_context"
            )

    def test_kind_int_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="h1", kind=1,  # type: ignore[arg-type]
                provenance="baseline_context",
            )

    def test_kind_valid_literals_pass(self) -> None:
        """All five production EvidenceKind values MUST be accepted."""
        valid_pairs = [
            ("initial_anchor", "initial_anchor"),
            ("read_range", "read_range"),
            ("search_hit", "search_current_article"),
            ("observation", "read_range"),
            ("article_seed", "baseline_context"),
        ]
        for kind, provenance in valid_pairs:
            obs = self._Obs(handle_id="h1", kind=kind, provenance=provenance)  # type: ignore[arg-type]
            assert obs.kind == kind

    # ------------------------------------------------------------------
    # provenance: Literal enum (reject typos)
    # ------------------------------------------------------------------

    def test_provenance_typo_rejected(self) -> None:
        """``"baseline"`` MUST be rejected (not in Literal set)."""
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="h1", kind="article_seed", provenance="baseline"
            )

    def test_provenance_typo_search_rejected(self) -> None:
        """``"search"`` MUST be rejected (not in Literal set)."""
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="h1", kind="search_hit", provenance="search"
            )

    def test_provenance_int_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="h1", kind="article_seed", provenance=1  # type: ignore[arg-type]
            )

    def test_provenance_valid_literals_pass(self) -> None:
        # P0-3: each provenance Literal value must be accepted when
        # paired with a LEGAL kind (see LEGAL_EVIDENCE_KIND_PROVENANCE).
        # The previous version of this test used ``kind="observation"``
        # for ALL four provenances, which incorrectly accepted the
        # illegal ``observation + baseline_context`` combination. Each
        # provenance is now paired with a kind that legally allows it.
        legal_pairs = [
            ("initial_anchor", "initial_anchor"),
            ("read_range", "read_range"),
            ("search_hit", "search_current_article"),
            ("article_seed", "baseline_context"),
        ]
        for kind, prov in legal_pairs:
            obs = self._Obs(
                handle_id="h1", kind=kind, provenance=prov  # type: ignore[arg-type]
            )
            assert obs.provenance == prov

    # ------------------------------------------------------------------
    # snippet: StrictStr (reject bool/int/float, allow empty)
    # ------------------------------------------------------------------

    def test_snippet_bool_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="h1",
                kind="article_seed",
                snippet=True,  # type: ignore[arg-type]
                provenance="baseline_context",
            )

    def test_snippet_int_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._Obs(
                handle_id="h1",
                kind="article_seed",
                snippet=123,  # type: ignore[arg-type]
                provenance="baseline_context",
            )

    def test_snippet_empty_string_allowed(self) -> None:
        """An empty snippet is a valid server-side truncation result."""
        obs = self._Obs(
            handle_id="h1", kind="article_seed", snippet="", provenance="baseline_context"
        )
        assert obs.snippet == ""

    def test_snippet_default_is_empty(self) -> None:
        obs = self._Obs(
            handle_id="h1", kind="article_seed", provenance="baseline_context"
        )
        assert obs.snippet == ""

    # ------------------------------------------------------------------
    # Nested DTO validation: resolved_evidence / all_evidence_observations
    # ------------------------------------------------------------------

    def test_nested_observation_invalid_kind_rejected_in_artifact(self) -> None:
        """An invalid kind in ``all_evidence_observations`` MUST reject
        the entire ``RawArtifact`` (nested strict validation).
        """
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c",
                run_id="r",
                all_evidence_observations=[
                    RawEvidenceObservation(
                        handle_id="h1",
                        kind="search_hits",  # type: ignore[arg-type]
                        provenance="search_current_article",
                    ),
                ],
            )

    def test_nested_observation_invalid_provenance_rejected_in_artifact(
        self,
    ) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c",
                run_id="r",
                resolved_evidence=[
                    RawEvidenceObservation(
                        handle_id="h1",
                        kind="article_seed",
                        provenance="baseline",  # type: ignore[arg-type]
                    ),
                ],
            )

    def test_nested_observation_empty_handle_rejected_in_artifact(self) -> None:
        with pytest.raises(ValidationError):
            RawArtifact(
                case_id="c",
                run_id="r",
                all_evidence_observations=[
                    RawEvidenceObservation(
                        handle_id="",
                        kind="article_seed",
                        provenance="baseline_context",
                    ),
                ],
            )

    def test_valid_evidence_observations_pass(self) -> None:
        """Non-regression: a well-formed evidence observation list
        is accepted.
        """
        artifact = RawArtifact(
            case_id="c",
            run_id="r",
            all_evidence_observations=[
                RawEvidenceObservation(
                    handle_id="h1",
                    kind="article_seed",
                    snippet="article snippet",
                    provenance="baseline_context",
                ),
                RawEvidenceObservation(
                    handle_id="h2",
                    kind="search_hit",
                    snippet="search result",
                    provenance="search_current_article",
                ),
            ],
        )
        assert len(artifact.all_evidence_observations) == 2
        assert artifact.all_evidence_observations[0].handle_id == "h1"
        assert artifact.all_evidence_observations[1].kind == "search_hit"


# ===========================================================================
# SECTION 6: P0-2 Non-regression — valid artifact with full evaluator input
# ===========================================================================


class TestRawArtifactValidEvaluatorInputNonRegression:
    """P0-2: A well-formed artifact with all evaluator-scored fields
    populated MUST construct without error.

    This is the non-regression guard: the strict validators must not
    reject well-formed inputs that the evaluators depend on.
    """

    def test_full_evaluator_input_artifact_constructs(self) -> None:
        """An artifact with every evaluator-scored field populated
        with valid values MUST construct successfully.
        """
        artifact = RawArtifact(
            case_id="case-a",
            run_id="phase1-test",
            run_index=0,
            finalized_status="ok",
            response_kind="grounded_answer",
            baseline_status="injected",
            baseline_is_complete=True,
            baseline_is_injected=True,
            read_range_calls=2,
            search_current_article_calls=1,
            cited_evidence_handles=["h1", "h2"],
            all_evidence_observations=[
                RawEvidenceObservation(
                    handle_id="h1",
                    kind="article_seed",
                    snippet="article snippet",
                    provenance="baseline_context",
                ),
                RawEvidenceObservation(
                    handle_id="h2",
                    kind="search_hit",
                    snippet="search result",
                    provenance="search_current_article",
                ),
            ],
            latency_seconds=1.5,
            agent_usage=RawUsage(
                requests=1,
                input_tokens=10,
                output_tokens=20,
            ),
            model_route="deepseek",
            final_text="这是一篇关于加拿大野火的测试回答。",
        )
        assert artifact.finalized_status == "ok"
        assert artifact.response_kind == "grounded_answer"
        assert artifact.baseline_status == "injected"
        assert artifact.baseline_is_complete is True
        assert artifact.baseline_is_injected is True
        assert artifact.read_range_calls == 2
        assert artifact.search_current_article_calls == 1
        assert artifact.cited_evidence_handles == ["h1", "h2"]
        assert len(artifact.all_evidence_observations) == 2
        assert artifact.latency_seconds == 1.5
        assert artifact.agent_usage is not None
        assert artifact.agent_usage.requests == 1


# ===========================================================================
# SECTION 7: P1 UTF-8 / empty / binary / BOM decoding (artifact side)
#
# load_artifacts_with_audit MUST classify decoding failures as
# invalid_json_count instead of raising UnicodeDecodeError.
# ===========================================================================


class TestArtifactLoadAuditUTF8Decoding:
    """P1: Invalid UTF-8 / truncated multi-byte / empty / binary / BOM
    files are classified as ``invalid_json_count`` — the loader MUST
    NOT raise ``UnicodeDecodeError``.
    """

    def test_invalid_utf8_bytes_counted_as_invalid_json(
        self, tmp_path: Path
    ) -> None:
        """Invalid UTF-8 bytes → ``invalid_json_count=1``.

        Previously ``read_text(encoding="utf-8")`` raised
        ``UnicodeDecodeError`` and crashed the aggregate.
        """
        artifact_dir = tmp_path / "artifacts"
        # 0xFF 0xFE is not valid UTF-8.
        _write_artifact_json(artifact_dir, "bad_utf8.json", b"\xff\xfe\x00\x01")
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.discovered_file_count == 1
        assert result.invalid_json_count == 1
        assert result.invalid_schema_count == 0
        assert result.foreign_run_id_count == 0
        assert result.invalid_artifact_count == 1
        assert result.is_clean is False
        assert len(result.valid_artifacts) == 0

    def test_truncated_multibyte_sequence_counted_as_invalid_json(
        self, tmp_path: Path
    ) -> None:
        """A truncated multi-byte UTF-8 sequence → ``invalid_json_count=1``.

        ``b"\xe4\xb8"`` is the first two bytes of a 3-byte Chinese
        character (``\xe4\xb8\xad`` = 中); the truncated 2-byte
        sequence is invalid UTF-8.
        """
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(artifact_dir, "truncated.json", b'{"k": "\xe4\xb8"}')
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.invalid_json_count == 1
        assert result.is_clean is False

    def test_empty_file_counted_as_invalid_json(self, tmp_path: Path) -> None:
        """An empty file → ``invalid_json_count=1``.

        ``read_text`` returns ``""``, then ``json.loads("")`` raises
        ``JSONDecodeError`` (caught by the existing handler).
        """
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(artifact_dir, "empty.json", "")
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.invalid_json_count == 1
        assert result.is_clean is False

    def test_binary_bytes_counted_as_invalid_json(self, tmp_path: Path) -> None:
        """Binary bytes → ``invalid_json_count=1``."""
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(
            artifact_dir,
            "binary.json",
            b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d",
        )
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.invalid_json_count == 1
        assert result.is_clean is False

    def test_utf8_bom_counted_as_invalid_json(self, tmp_path: Path) -> None:
        """UTF-8 BOM → ``invalid_json_count=1``.

        Policy: strict UTF-8 — the BOM (``\\xef\\xbb\\xbf``) is NOT
        stripped. ``json.loads`` fails on the BOM char and the file is
        classified as ``invalid_json``. This is the fail-closed choice:
        operators who write artifacts must write plain UTF-8 without BOM.
        """
        artifact_dir = tmp_path / "artifacts"
        # UTF-8 BOM + valid JSON
        bom_json = b"\xef\xbb\xbf" + b'{"case_id": "c", "run_id": "phase1-test"}'
        _write_artifact_json(artifact_dir, "bom.json", bom_json)
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        # BOM causes json.loads to fail (the BOM char is not valid JSON
        # syntax) — classified as invalid_json.
        assert result.invalid_json_count == 1
        assert result.invalid_schema_count == 0
        assert result.is_clean is False

    def test_decoding_failure_does_not_raise(self, tmp_path: Path) -> None:
        """The loader MUST NOT raise — it MUST classify and continue.

        Multiple invalid-UTF-8 files in the same directory must all
        be counted, and the loader must process subsequent valid files.
        """
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(artifact_dir, "bad1.json", b"\xff\xfe")
        _write_artifact_json(artifact_dir, "bad2.json", b"\xe4\xb8")
        # A valid artifact in the same directory should still load.
        _write_artifact_json(
            artifact_dir,
            "good.json",
            _make_valid_artifact().model_dump(),
        )
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.discovered_file_count == 3
        assert result.invalid_json_count == 2
        assert len(result.valid_artifacts) == 1
        assert result.valid_artifacts[0].case_id == "case-a"

    def test_decoding_failure_result_does_not_leak_exception_text(
        self, tmp_path: Path
    ) -> None:
        """The ``ArtifactLoadResult`` MUST NOT carry exception text,
        file paths, or byte content. Only typed counts are exposed.

        A single ``exception_type=<ClassName>`` is printed to stderr
        for operator diagnostics — that is the only diagnostic output.

        Note: ``load_artifacts_with_audit`` caches ``sys.stderr`` as a
        default parameter at function definition time, so neither
        ``capsys`` (python-level) nor ``capfd`` (fd-level) reliably
        captures the warning under all pytest configurations. The
        function exposes a private ``_stderr`` testing seam for exactly
        this scenario — we inject a ``StringIO`` and read it back.
        """
        import io

        artifact_dir = tmp_path / "artifacts"
        # Embed fake "sensitive" bytes to prove they don't leak.
        _write_artifact_json(
            artifact_dir,
            "secret.json",
            b"\xffSECRET_API_KEY=sk-FAKE-DO-NOT-LEAK\xff",
        )
        stderr_sink = io.StringIO()
        result = load_artifacts_with_audit(
            artifact_dir, "phase1-test", _stderr=stderr_sink
        )
        # The result is a frozen dataclass with only counts + artifacts.
        assert result.invalid_json_count == 1
        # The result has NO field that could carry exception text or bytes.
        assert not hasattr(result, "error_text")
        assert not hasattr(result, "exception")
        assert not hasattr(result, "raw_bytes")
        assert not hasattr(result, "file_paths")
        # stderr diagnostic carries only the exception type name, not
        # the bytes or path.
        err = stderr_sink.getvalue()
        assert "UnicodeDecodeError" in err or "JSONDecodeError" in err
        assert "sk-FAKE-DO-NOT-LEAK" not in err
        # The file path MAY appear in stderr (for operator diagnostics),
        # but MUST NOT appear in the result dataclass.


# ===========================================================================
# SECTION 8: P1 Aggregate — invalid encoding → blocked_incomplete
# ===========================================================================


class TestAggregateUTF8DecodingIncomplete:
    """P1: An invalid-UTF-8 artifact (or manifest) forces the aggregate
    verdict to ``blocked_incomplete_real_model_run`` — the run started
    but its audit trail is broken.
    """

    def test_absent_manifest_plus_invalid_utf8_artifact_yields_incomplete(
        self, tmp_path: Path
    ) -> None:
        """absent manifest + invalid UTF-8 artifact →
        ``blocked_incomplete_real_model_run``.

        The artifact load is not clean (invalid_json_count > 0), so
        the readiness audit blocks the normal verdict path.
        """
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(artifact_dir, "bad.json", b"\xff\xfe\x00")
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.is_clean is False

        readiness = _make_readiness(
            artifact_load_clean=False,
            discovered_file_count=1,
            invalid_artifact_count=1,
            manifest_state="absent",
            manifest_present=False,
            manifest_run_id_matches=None,
            planned_count=0,
            evaluable_artifact_count=0,
            evaluated_case_result_count=0,
        )
        coverage = _make_coverage_audit(
            manifest_present=False,
            manifest_state="absent",
            manifest_run_id_matches=None,
            planned_count=0,
            evaluable_artifact_count=0,
        )
        verdict, _, _ = _RUNNER._decide_final_verdict(
            case_results=[],
            coverage_audit=coverage,
            identity_mismatched_count=0,
            real_model_blocked=True,
            has_budget_exhausted=False,
            total_artifacts_loaded=1,
            readiness=readiness,
        )
        assert verdict == "blocked_incomplete_real_model_run"

    def test_invalid_utf8_artifact_does_not_crash_aggregate(
        self, tmp_path: Path
    ) -> None:
        """The aggregate MUST NOT crash when an artifact file has
        invalid UTF-8 — the loader classifies it and the aggregate
        proceeds to the ``blocked_incomplete`` verdict.
        """
        artifact_dir = tmp_path / "artifacts"
        # Mix of invalid and valid artifacts.
        _write_artifact_json(artifact_dir, "bad.json", b"\xff\xfe")
        _write_artifact_json(
            artifact_dir,
            "good.json",
            _make_valid_artifact().model_dump(),
        )
        # This call MUST NOT raise.
        result = load_artifacts_with_audit(artifact_dir, "phase1-test")
        assert result.discovered_file_count == 2
        assert result.invalid_json_count == 1
        assert len(result.valid_artifacts) == 1


# ===========================================================================
# SECTION 9: P0-2 Schema-rejected vs evaluator-preserved boundary
#
# Proves the semantic boundary from spec section 三:
# - Type/format invalid → rejected at artifact load boundary
# - Content quality poor → preserved for evaluator to fail
# ===========================================================================


class TestSchemaRejectedVsEvaluatorPreservedBoundary:
    """P0-2: The schema rejects TYPE/FORMAT corruption but PRESERVES
    content-quality issues for the evaluator to detect.

    This is the key semantic boundary from spec section 三:
    - ``kind/provenance`` typo → schema rejects (contract corruption)
    - ``tool call count "1"`` → schema rejects (type corruption)
    - duplicate cited handle → schema preserves (content quality)
    - unknown cited handle → schema preserves (content quality)
    - empty final_text → schema preserves (content quality)
    """

    def test_kind_typo_is_schema_corruption(self) -> None:
        """A typo'd evidence kind is a CONTRACT corruption — the
        schema rejects it. The evaluator never sees it."""
        with pytest.raises(ValidationError):
            RawEvidenceObservation(
                handle_id="h1",
                kind="search_hits",  # typo — schema rejects
                provenance="search_current_article",
            )

    def test_provenance_typo_is_schema_corruption(self) -> None:
        with pytest.raises(ValidationError):
            RawEvidenceObservation(
                handle_id="h1",
                kind="article_seed",
                provenance="baseline",  # typo — schema rejects
            )

    def test_tool_call_count_string_is_schema_corruption(self) -> None:
        """``read_range_calls="1"`` is a TYPE corruption — the schema
        rejects it. The evaluator never sees it."""
        with pytest.raises(ValidationError):
            RawArtifact(case_id="c", run_id="r", read_range_calls="1")  # type: ignore[arg-type]

    def test_duplicate_cited_handle_is_content_quality(self) -> None:
        """Duplicate cited handles are CONTENT quality — the schema
        PRESERVES them for the ``evidence_minimality`` evaluator to
        fail. The schema must NOT reject duplicates.
        """
        artifact = RawArtifact(
            case_id="c", run_id="r", cited_evidence_handles=["h1", "h1"]
        )
        # Schema accepted — evaluator will fail.
        assert len(artifact.cited_evidence_handles) == 2

    def test_unknown_cited_handle_is_content_quality(self) -> None:
        """Unknown cited handles are CONTENT quality — the schema
        PRESERVES them for the ``evidence_minimality`` evaluator to
        fail.
        """
        artifact = RawArtifact(
            case_id="c", run_id="r", cited_evidence_handles=["unknown"]
        )
        # Schema accepted — evaluator will fail.
        assert artifact.cited_evidence_handles == ["unknown"]

    def test_empty_final_text_is_content_quality(self) -> None:
        """An empty ``final_text`` is CONTENT quality — the schema
        PRESERVES it for the ``answer_success`` evaluator to fail.
        """
        artifact = RawArtifact(case_id="c", run_id="r", final_text="")
        # Schema accepted — evaluator will fail.
        assert artifact.final_text == ""

    def test_none_final_text_is_content_quality(self) -> None:
        """A ``None`` ``final_text`` is CONTENT quality — the schema
        PRESERVES it for the evaluator to fail.
        """
        artifact = RawArtifact(case_id="c", run_id="r", final_text=None)
        assert artifact.final_text is None


# ===========================================================================
# SECTION 10: P0-2 Coercion regression THROUGH real evaluators
#
# Spec section 四 requires tests that go "穿过真实 evaluator" — proving
# that coercion no longer changes scoring results. The strict schema
# (SECTION 4-5) already proves type corruption is rejected at the
# artifact load boundary. This section proves the END-TO-END path:
#
# 1. A coerced artifact FILE (e.g. ``read_range_calls="1"``) is
#    classified as ``invalid_schema_count`` by
#    :func:`load_artifacts_with_audit`, so it NEVER reaches the
#    evaluator. The tool_decision evaluator cannot mistakenly pass a
#    required-tool case because the artifact is rejected before the
#    evaluator sees it.
#
# 2. A schema-valid but content-quality-poor artifact (duplicate
#    cited handles, unknown cited handles) DOES reach the evaluator
#    and the evaluator correctly returns ``passed=False``.
#
# 3. A normal, schema-valid artifact passes through all 11
#    dimensions without regression.
# ===========================================================================


class TestCoercionRegressionThroughEvaluators:
    """P0-2 spec section 四: prove coercion no longer changes scoring.

    Each test writes an artifact JSON FILE (not just a RawArtifact
    constructor call), runs it through :func:`load_artifacts_with_audit`
    (the production load boundary), then — if the artifact survives —
    invokes the real evaluator. This proves the END-TO-END contract:
    schema-rejected corruption never reaches evaluators, while
    content-quality issues do.
    """

    def _write_and_load(
        self, tmp_path: Path, payload: dict
    ) -> ArtifactLoadResult:
        """Write a single artifact JSON file and load it through the
        production load boundary.
        """
        artifact_dir = tmp_path / "artifacts"
        _write_artifact_json(artifact_dir, "case-a__0.json", payload)
        return load_artifacts_with_audit(artifact_dir, "phase1-test")

    # ------------------------------------------------------------------
    # (1) read_range_calls="1" must NOT let a required-tool case pass
    # ------------------------------------------------------------------

    def test_read_range_calls_string_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """``read_range_calls="1"`` is type corruption — the load
        boundary classifies it as ``invalid_schema_count`` so the
        ``tool_decision`` evaluator never sees it (and therefore cannot
        mistakenly pass a required-tool case).
        """
        payload = _make_valid_artifact().model_dump()
        payload["read_range_calls"] = "1"  # type: ignore[assignment]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    def test_read_range_calls_true_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """``read_range_calls=True`` would be coerced to ``1`` under
        the old lenient schema, letting a required-tool case falsely
        pass. The strict schema rejects it at the load boundary.
        """
        payload = _make_valid_artifact().model_dump()
        payload["read_range_calls"] = True  # type: ignore[assignment]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    def test_search_current_article_calls_float_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """``search_current_article_calls=1.0`` is type corruption —
        rejected at the load boundary, never reaches ``tool_decision``.
        """
        payload = _make_valid_artifact().model_dump()
        payload["search_current_article_calls"] = 1.0  # type: ignore[assignment]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    def test_baseline_is_complete_string_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """``baseline_is_complete="true"`` is type corruption —
        rejected at the load boundary. The ``evidence_minimality``
        soft-failure branch (which checks ``is True``) never sees a
        coerced value, so the evaluation result cannot be altered.
        """
        payload = _make_valid_artifact().model_dump()
        payload["baseline_is_complete"] = "true"  # type: ignore[assignment]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    def test_baseline_is_injected_int_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """``baseline_is_injected=1`` is type corruption — rejected
        at the load boundary.
        """
        payload = _make_valid_artifact().model_dump()
        payload["baseline_is_injected"] = 1  # type: ignore[assignment]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    def test_latency_seconds_string_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """``latency_seconds="1"`` is type corruption — rejected at
        the load boundary.
        """
        payload = _make_valid_artifact().model_dump()
        payload["latency_seconds"] = "1"  # type: ignore[assignment]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    def test_latency_seconds_true_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """``latency_seconds=True`` would be coerced to ``1.0`` under
        the old lenient schema. Strict schema rejects it.
        """
        payload = _make_valid_artifact().model_dump()
        payload["latency_seconds"] = True  # type: ignore[assignment]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    # ------------------------------------------------------------------
    # (2) Invalid evidence kind/provenance must NOT enter evaluator
    # ------------------------------------------------------------------

    def test_invalid_evidence_kind_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """An invalid evidence kind (e.g. ``"search_hits"`` typo) is
        contract corruption — rejected at the load boundary. The
        ``evidence_minimality`` evaluator never sees it.
        """
        payload = _make_valid_artifact().model_dump()
        payload["all_evidence_observations"] = [
            {
                "handle_id": "h1",
                "kind": "search_hits",  # typo
                "snippet": "s",
                "provenance": "search_current_article",
            }
        ]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    def test_invalid_evidence_provenance_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """An invalid evidence provenance (e.g. ``"baseline"`` typo)
        is contract corruption — rejected at the load boundary.
        """
        payload = _make_valid_artifact().model_dump()
        payload["all_evidence_observations"] = [
            {
                "handle_id": "h1",
                "kind": "article_seed",
                "snippet": "s",
                "provenance": "baseline",  # typo
            }
        ]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    def test_empty_evidence_handle_rejected_at_load_boundary(
        self, tmp_path: Path
    ) -> None:
        """An empty ``handle_id`` is contract corruption — rejected
        at the load boundary.
        """
        payload = _make_valid_artifact().model_dump()
        payload["all_evidence_observations"] = [
            {
                "handle_id": "",  # empty
                "kind": "article_seed",
                "snippet": "s",
                "provenance": "baseline_context",
            }
        ]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 1
        assert len(result.valid_artifacts) == 0

    # ------------------------------------------------------------------
    # (3) Schema-valid but content-quality-poor artifacts DO reach
    #     the evaluator and the evaluator correctly fails them.
    # ------------------------------------------------------------------

    def test_duplicate_cited_handles_reach_evaluator_and_fail(
        self, tmp_path: Path
    ) -> None:
        """Duplicate cited handles are CONTENT quality — schema
        preserves them, the ``evidence_minimality`` evaluator fails.
        """
        from claread_eval.reader_record_ask.evaluators.evidence_minimality import (
            evaluate_evidence_minimality,
        )

        handle = "evh_a" + "0" * 29
        payload = _make_valid_artifact().model_dump()
        payload["cited_evidence_handles"] = [handle, handle]
        payload["all_evidence_observations"] = [
            {
                "handle_id": handle,
                "kind": "article_seed",
                "snippet": "s",
                "provenance": "baseline_context",
            }
        ]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 0
        assert len(result.valid_artifacts) == 1

        # Evaluator IS called and correctly fails.
        artifact = result.valid_artifacts[0]
        case = _make_case()
        eval_result = evaluate_evidence_minimality(case, artifact)
        assert eval_result.passed is False
        assert eval_result.severity == "high"
        assert "duplicate" in eval_result.details

    def test_unknown_cited_handle_reaches_evaluator_and_fail(
        self, tmp_path: Path
    ) -> None:
        """Unknown cited handles are CONTENT quality — schema
        preserves them, the ``evidence_minimality`` evaluator fails.
        """
        from claread_eval.reader_record_ask.evaluators.evidence_minimality import (
            evaluate_evidence_minimality,
        )

        known = "evh_a" + "0" * 29
        unknown = "evh_z" + "0" * 29
        payload = _make_valid_artifact().model_dump()
        payload["cited_evidence_handles"] = [known, unknown]
        payload["all_evidence_observations"] = [
            {
                "handle_id": known,
                "kind": "article_seed",
                "snippet": "s",
                "provenance": "baseline_context",
            }
        ]
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 0
        assert len(result.valid_artifacts) == 1

        # Evaluator IS called and correctly fails.
        artifact = result.valid_artifacts[0]
        case = _make_case()
        eval_result = evaluate_evidence_minimality(case, artifact)
        assert eval_result.passed is False
        assert eval_result.severity == "high"
        assert "not in observations" in eval_result.details

    # ------------------------------------------------------------------
    # (4) Normal artifact — 11-dim evaluation runs without regression
    # ------------------------------------------------------------------

    def test_normal_artifact_passes_through_evaluators_without_regression(
        self, tmp_path: Path
    ) -> None:
        """A schema-valid artifact with healthy content passes through
        the ``evidence_minimality`` and ``tool_decision`` evaluators
        without regression — both return their expected verdicts.
        """
        from claread_eval.reader_record_ask.evaluators.evidence_minimality import (
            evaluate_evidence_minimality,
        )
        from claread_eval.reader_record_ask.evaluators.tool_decision import (
            evaluate_tool_decision,
        )

        h1 = "evh_a" + "0" * 29
        h2 = "evh_b" + "0" * 29
        payload = _make_valid_artifact().model_dump()
        payload["cited_evidence_handles"] = [h1, h2]
        payload["all_evidence_observations"] = [
            {
                "handle_id": h1,
                "kind": "article_seed",
                "snippet": "article snippet",
                "provenance": "baseline_context",
            },
            {
                "handle_id": h2,
                "kind": "search_hit",
                "snippet": "search result",
                "provenance": "search_current_article",
            },
        ]
        payload["read_range_calls"] = 1
        payload["search_current_article_calls"] = 1
        payload["baseline_is_complete"] = True
        result = self._write_and_load(tmp_path, payload)
        assert result.invalid_schema_count == 0
        assert len(result.valid_artifacts) == 1

        artifact = result.valid_artifacts[0]
        case = _make_case()

        # evidence_minimality: 2 handles, no duplicates, all resolved.
        em = evaluate_evidence_minimality(case, artifact)
        assert em.passed is True
        assert em.severity == "none"

        # tool_decision: case default expect_tool_calls="optional" → pass.
        td = evaluate_tool_decision(case, artifact)
        assert td.passed is True
        assert "read_range_calls=1" in td.details
        assert "search_current_article_calls=1" in td.details


# ===========================================================================
# SECTION 11: P1 Manifest UTF-8 decoding fail-closed
#
# Spec section 五: ``read_manifest_with_state`` must catch
# ``UnicodeDecodeError`` / ``UnicodeError`` and classify as
# ``ManifestState.CORRUPT`` (not raise, not absent).
# ===========================================================================


class TestManifestUTF8DecodingFailClosed:
    """P1: ``read_manifest_with_state`` classifies UTF-8 decoding
    failures as ``ManifestState.CORRUPT`` — never raises, never
    classifies as ``ABSENT``.
    """

    def _write_manifest_bytes(self, tmp_path: Path, data: bytes) -> Path:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_bytes(data)
        return manifest_path

    def test_invalid_utf8_bytes_classified_as_corrupt(
        self, tmp_path: Path
    ) -> None:
        """Invalid UTF-8 bytes (0xFF 0xFE) → CORRUPT, not raise."""
        from claread_eval.reader_record_ask.run_manifest import (
            ManifestState,
            read_manifest_with_state,
        )

        path = self._write_manifest_bytes(tmp_path, b'\xff\xfe{"run_id": "x"}')
        result = read_manifest_with_state(path)
        assert result.state == ManifestState.CORRUPT
        assert result.manifest is None

    def test_truncated_multibyte_sequence_classified_as_corrupt(
        self, tmp_path: Path
    ) -> None:
        """A truncated multi-byte UTF-8 sequence → CORRUPT."""
        from claread_eval.reader_record_ask.run_manifest import (
            ManifestState,
            read_manifest_with_state,
        )

        # 0xE4 0xB8 starts a 3-byte Chinese char; missing the 3rd byte.
        path = self._write_manifest_bytes(tmp_path, b'{"k": "\xe4\xb8"}')
        result = read_manifest_with_state(path)
        assert result.state == ManifestState.CORRUPT
        assert result.manifest is None

    def test_empty_file_classified_as_corrupt(
        self, tmp_path: Path
    ) -> None:
        """An empty manifest file → CORRUPT (json.loads fails on
        empty string), not ABSENT (the file exists).
        """
        from claread_eval.reader_record_ask.run_manifest import (
            ManifestState,
            read_manifest_with_state,
        )

        path = self._write_manifest_bytes(tmp_path, b"")
        result = read_manifest_with_state(path)
        assert result.state == ManifestState.CORRUPT
        assert result.manifest is None

    def test_binary_bytes_classified_as_corrupt(
        self, tmp_path: Path
    ) -> None:
        """Pure binary bytes → CORRUPT."""
        from claread_eval.reader_record_ask.run_manifest import (
            ManifestState,
            read_manifest_with_state,
        )

        path = self._write_manifest_bytes(
            tmp_path, b"\x00\x01\x02\x03\xff\xfe\xfd"
        )
        result = read_manifest_with_state(path)
        assert result.state == ManifestState.CORRUPT
        assert result.manifest is None

    def test_utf8_bom_classified_as_corrupt(
        self, tmp_path: Path
    ) -> None:
        """UTF-8 BOM (``\xef\xbb\xbf``) prefix → CORRUPT.

        Policy decision (spec section 五): strict UTF-8 is enforced.
        The BOM is NOT stripped; ``json.loads`` fails on the BOM char
        and the file is classified as CORRUPT (fail-closed). This is
        a deliberate fail-closed choice — operators must save manifests
        as plain UTF-8 without BOM.
        """
        from claread_eval.reader_record_ask.run_manifest import (
            ManifestState,
            read_manifest_with_state,
        )

        bom = b"\xef\xbb\xbf"
        valid_json = b'{"schema_version": "test", "run_id": "x"}'
        path = self._write_manifest_bytes(tmp_path, bom + valid_json)
        result = read_manifest_with_state(path)
        assert result.state == ManifestState.CORRUPT
        assert result.manifest is None

    def test_valid_manifest_still_loads(
        self, tmp_path: Path
    ) -> None:
        """Non-regression: a valid UTF-8 JSON manifest still loads
        as VALID after the strict decoding changes.
        """
        from claread_eval.reader_record_ask.run_manifest import (
            ManifestState,
            read_manifest_with_state,
        )

        path = tmp_path / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": MANIFEST_SCHEMA_VERSION,
                    "run_id": "x",
                    "phase": 1,
                    "dataset_id": "ds",
                    "dataset_schema_version": "v1",
                    "dataset_content_sha256": "a" * 64,
                    "status": "completed",
                    "planned_run_indices": {"c": [0]},
                    "completed_run_indices": {"c": [0]},
                    "remaining_run_indices": {},
                    "executed_requests": 0,
                    "executed_tokens": 0,
                    "stop_reason": None,
                }
            ),
            encoding="utf-8",
        )
        result = read_manifest_with_state(path)
        assert result.state == ManifestState.VALID
        assert result.manifest is not None
        assert result.manifest.run_id == "x"
