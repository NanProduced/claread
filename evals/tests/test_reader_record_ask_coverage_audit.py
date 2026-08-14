"""Aggregate coverage audit tests.

Spec: the accepted aggregate coverage audit contract.
Requirement: Aggregate Coverage Audit.

Drives :func:`validate_manifest_coverage` directly with minimal
``SimpleNamespace`` artifact stubs and ``ReaderRecordAskRunManifest``
fixtures. Covers all 8 spec scenarios plus 2 integration scenarios:

1. complete manifest + exact artifacts → audit passes
2. planned artifact missing → missing_count=1
3. duplicate (case_id, run_index) → duplicate_count=1
4. unexpected artifact not in planned → unexpected_count=1
5. partial artifacts + budget_exhausted manifest → missing=2,
   manifest_status="budget_exhausted"
6. partial artifacts + missing manifest → manifest_present=False,
   planned_count=0, evaluable=3
7. completed manifest + artifact identity mismatch →
   identity_mismatch_count=1, evaluable decremented
8. no manifest + no artifacts → all counts 0
9. integration: write manifest via RunSessionLayout.manifest_path,
   read back via read_manifest, validate_manifest_coverage works
10. corrupt manifest → read_manifest raises RunManifestError;
    aggregate (in a follow-up test in the verdict suite) treats as
    manifest=None

No real LLM / provider calls. Artifacts use ``SimpleNamespace`` mocks.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from claread_eval.reader_record_ask.run_manifest import (
    MANIFEST_SCHEMA_VERSION,
    CoverageAuditResult,
    ReaderRecordAskRunManifest,
    RunManifestError,
    read_manifest,
    validate_manifest_coverage,
    write_manifest_atomic,
)
from claread_eval.reader_record_ask.session import RunSessionLayout

# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

_DS_ID = "reader-record-ask-r4-a3"
_DS_SCHEMA_VERSION = "dataset.schema/v1"
_DS_SHA = "a" * 64


def _make_artifact(
    *,
    case_id: str,
    run_index: int,
    dataset_id: str = _DS_ID,
    dataset_schema_version: str = _DS_SCHEMA_VERSION,
    dataset_content_sha256: str = _DS_SHA,
) -> SimpleNamespace:
    """Minimal artifact stub carrying the fields coverage audit reads."""
    return SimpleNamespace(
        case_id=case_id,
        run_index=run_index,
        dataset_id=dataset_id,
        dataset_schema_version=dataset_schema_version,
        dataset_content_sha256=dataset_content_sha256,
    )


def _make_manifest(
    *,
    status: str = "completed",
    planned: dict[str, list[int]] | None = None,
    completed: dict[str, list[int]] | None = None,
    remaining: dict[str, list[int]] | None = None,
    run_id: str = "phase1-abc",
    phase: int = 1,
    dataset_id: str = _DS_ID,
    dataset_schema_version: str = _DS_SCHEMA_VERSION,
    dataset_content_sha256: str = _DS_SHA,
    executed_requests: int = 0,
    executed_tokens: int = 0,
    stop_reason: str | None = None,
) -> ReaderRecordAskRunManifest:
    if planned is None:
        planned = {"c1": [0, 1]}
    if completed is None:
        if status == "completed":
            completed = {k: list(v) for k, v in planned.items()}
        else:
            completed = {}
    if remaining is None:
        if status == "completed":
            remaining = {}
        else:
            remaining = {k: list(v) for k, v in planned.items()}
    if stop_reason is None and status == "budget_exhausted":
        stop_reason = "budget_exhausted"
    return ReaderRecordAskRunManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id=run_id,
        phase=phase,
        dataset_id=dataset_id,
        dataset_schema_version=dataset_schema_version,
        dataset_content_sha256=dataset_content_sha256,
        status=status,  # type: ignore[arg-type]
        planned_run_indices=planned,
        completed_run_indices=completed,
        remaining_run_indices=remaining,
        executed_requests=executed_requests,
        executed_tokens=executed_tokens,
        stop_reason=stop_reason,
    )


# ---------------------------------------------------------------------------
# 1: complete manifest + exact artifacts
# ---------------------------------------------------------------------------


def test_coverage_audit_complete_manifest_exact_artifacts() -> None:
    """planned=[(c1,[0,1]),(c2,[0,1])], 4 artifacts all match → all 0."""
    manifest = _make_manifest(
        status="completed",
        planned={"c1": [0, 1], "c2": [0, 1]},
        completed={"c1": [0, 1], "c2": [0, 1]},
        remaining={},
    )
    arts = [
        _make_artifact(case_id="c1", run_index=0),
        _make_artifact(case_id="c1", run_index=1),
        _make_artifact(case_id="c2", run_index=0),
        _make_artifact(case_id="c2", run_index=1),
    ]
    result = validate_manifest_coverage(manifest, arts)
    assert isinstance(result, CoverageAuditResult)
    assert result.manifest_present is True
    assert result.manifest_status == "completed"
    assert result.planned_count == 4
    assert result.completed_count == 4
    assert result.missing_count == 0
    assert result.duplicate_count == 0
    assert result.unexpected_count == 0
    assert result.identity_mismatch_count == 0
    assert result.evaluable_artifact_count == 4
    assert result.dataset_identity == (_DS_ID, _DS_SCHEMA_VERSION, _DS_SHA)


# ---------------------------------------------------------------------------
# 2: planned artifact missing
# ---------------------------------------------------------------------------


def test_coverage_audit_planned_artifact_missing() -> None:
    """planned 4 but only 3 artifacts → missing=1, evaluable=3."""
    manifest = _make_manifest(
        status="completed",
        planned={"c1": [0, 1], "c2": [0, 1]},
        completed={"c1": [0, 1], "c2": [0, 1]},
        remaining={},
    )
    arts = [
        _make_artifact(case_id="c1", run_index=0),
        _make_artifact(case_id="c1", run_index=1),
        _make_artifact(case_id="c2", run_index=0),
        # c2[1] missing
    ]
    result = validate_manifest_coverage(manifest, arts)
    assert result.missing_count == 1
    assert result.missing_run_indices == {"c2": [1]}
    assert result.evaluable_artifact_count == 3
    assert result.duplicate_count == 0
    assert result.unexpected_count == 0
    assert result.identity_mismatch_count == 0


# ---------------------------------------------------------------------------
# 3: duplicate (case_id, run_index)
# ---------------------------------------------------------------------------


def test_coverage_audit_duplicate_case_run_index() -> None:
    """Two artifacts at same (c1, 0) → duplicate=1, evaluable decremented."""
    manifest = _make_manifest(
        status="completed",
        planned={"c1": [0, 1]},
        completed={"c1": [0, 1]},
        remaining={},
    )
    arts = [
        _make_artifact(case_id="c1", run_index=0),
        _make_artifact(case_id="c1", run_index=0),  # duplicate
        _make_artifact(case_id="c1", run_index=1),
    ]
    result = validate_manifest_coverage(manifest, arts)
    assert result.duplicate_count == 1
    assert result.duplicate_run_indices == {"c1": [0]}
    assert result.evaluable_artifact_count == 2  # 3 - 1 duplicate
    assert result.missing_count == 0
    assert result.unexpected_count == 0


# ---------------------------------------------------------------------------
# 4: unexpected artifact not in planned
# ---------------------------------------------------------------------------


def test_coverage_audit_unexpected_artifact_not_in_planned() -> None:
    """artifact (c3, 0) not in planned → unexpected=1, evaluable decremented."""
    manifest = _make_manifest(
        status="completed",
        planned={"c1": [0, 1]},
        completed={"c1": [0, 1]},
        remaining={},
    )
    arts = [
        _make_artifact(case_id="c1", run_index=0),
        _make_artifact(case_id="c1", run_index=1),
        _make_artifact(case_id="c3", run_index=0),  # not planned
    ]
    result = validate_manifest_coverage(manifest, arts)
    assert result.unexpected_count == 1
    assert result.unexpected_run_indices == {"c3": [0]}
    assert result.evaluable_artifact_count == 2  # 3 - 1 unexpected
    assert result.duplicate_count == 0
    assert result.missing_count == 0


# ---------------------------------------------------------------------------
# 5: partial artifacts + budget_exhausted manifest
# ---------------------------------------------------------------------------


def test_coverage_audit_partial_artifacts_with_budget_manifest() -> None:
    """status=budget_exhausted, planned=4, completed=2, artifacts=2.

    The manifest records a partial run. Coverage audit reflects the
    budget stop via manifest_status and counts the not-yet-completed
    indices as missing.
    """
    manifest = _make_manifest(
        status="budget_exhausted",
        planned={"c1": [0, 1], "c2": [0, 1]},
        completed={"c1": [0, 1]},
        remaining={"c2": [0, 1]},
        stop_reason="budget_exhausted",
    )
    arts = [
        _make_artifact(case_id="c1", run_index=0),
        _make_artifact(case_id="c1", run_index=1),
    ]
    result = validate_manifest_coverage(manifest, arts)
    assert result.manifest_present is True
    assert result.manifest_status == "budget_exhausted"
    assert result.planned_count == 4
    assert result.completed_count == 2
    assert result.missing_count == 2
    assert result.missing_run_indices == {"c2": [0, 1]}
    assert result.evaluable_artifact_count == 2
    assert result.identity_mismatch_count == 0


# ---------------------------------------------------------------------------
# 6: partial artifacts + missing manifest
# ---------------------------------------------------------------------------


def test_coverage_audit_partial_artifacts_missing_manifest() -> None:
    """manifest=None, 3 artifacts → manifest_present=False, planned=0,
    evaluable=3 (no manifest = nothing to compare against for missing/
    unexpected/identity_mismatch)."""
    arts = [
        _make_artifact(case_id="c1", run_index=0),
        _make_artifact(case_id="c1", run_index=1),
        _make_artifact(case_id="c2", run_index=0),
    ]
    result = validate_manifest_coverage(None, arts)
    assert result.manifest_present is False
    assert result.manifest_status is None
    assert result.planned_count == 0
    assert result.completed_count == 0
    assert result.missing_count == 0
    assert result.duplicate_count == 0
    assert result.unexpected_count == 0
    assert result.identity_mismatch_count == 0
    assert result.evaluable_artifact_count == 3
    assert result.dataset_identity is None


# ---------------------------------------------------------------------------
# 7: completed manifest + artifact identity mismatch
# ---------------------------------------------------------------------------


def test_coverage_audit_completed_manifest_artifact_identity_mismatch() -> None:
    """manifest identity=(d1,s1,c1), artifact identity=(d2,s2,c2) →
    identity_mismatch_count=1, evaluable decremented."""
    manifest = _make_manifest(
        status="completed",
        planned={"c1": [0, 1]},
        completed={"c1": [0, 1]},
        remaining={},
        dataset_id="ds-a",
        dataset_schema_version="v1",
        dataset_content_sha256="a" * 64,
    )
    art_matching = _make_artifact(
        case_id="c1", run_index=0,
        dataset_id="ds-a",
        dataset_schema_version="v1",
        dataset_content_sha256="a" * 64,
    )
    art_mismatched = _make_artifact(
        case_id="c1", run_index=1,
        dataset_id="ds-B",  # mismatch
        dataset_schema_version="v1",
        dataset_content_sha256="a" * 64,
    )
    result = validate_manifest_coverage(manifest, [art_matching, art_mismatched])
    assert result.identity_mismatch_count == 1
    assert result.evaluable_artifact_count == 1  # 2 - 1 mismatch
    assert result.dataset_identity == ("ds-a", "v1", "a" * 64)
    assert result.missing_count == 0
    assert result.duplicate_count == 0
    assert result.unexpected_count == 0


# ---------------------------------------------------------------------------
# 8: no manifest + no artifacts
# ---------------------------------------------------------------------------


def test_coverage_audit_no_manifest_no_artifacts() -> None:
    """manifest=None, artifacts=[] → all counts 0."""
    result = validate_manifest_coverage(None, [])
    assert result.manifest_present is False
    assert result.manifest_status is None
    assert result.planned_count == 0
    assert result.completed_count == 0
    assert result.missing_count == 0
    assert result.duplicate_count == 0
    assert result.unexpected_count == 0
    assert result.identity_mismatch_count == 0
    assert result.evaluable_artifact_count == 0
    assert result.dataset_identity is None
    assert result.missing_run_indices == {}
    assert result.duplicate_run_indices == {}
    assert result.unexpected_run_indices == {}


# ---------------------------------------------------------------------------
# 9: integration — RunSessionLayout.manifest_path resolver
# ---------------------------------------------------------------------------


def test_coverage_audit_run_session_layout_manifest_path_integration(
    tmp_path: Path,
) -> None:
    """Write manifest via RunSessionLayout.manifest_path, read it back,
    then validate_manifest_coverage works end-to-end.

    This proves the writer/reader path contract: both harness (writer)
    and aggregate (reader) MUST use the same RunSessionLayout.
    manifest_path resolver — they MUST NOT hand-build the path.
    """
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-int")
    manifest = _make_manifest(
        status="completed",
        run_id="phase1-int",
        planned={"c1": [0, 1]},
        completed={"c1": [0, 1]},
        remaining={},
    )
    # Writer uses layout.manifest_path — single source of truth.
    path = layout.manifest_path
    assert path == tmp_path / "phase1-int" / "manifest.json"
    write_manifest_atomic(manifest, path)

    # Reader uses the SAME resolver — no hand-built path.
    back = read_manifest(layout.manifest_path)
    assert back is not None
    assert back == manifest

    # Coverage audit works on the read-back manifest.
    arts = [
        _make_artifact(case_id="c1", run_index=0),
        _make_artifact(case_id="c1", run_index=1),
    ]
    result = validate_manifest_coverage(back, arts)
    assert result.manifest_present is True
    assert result.manifest_status == "completed"
    assert result.missing_count == 0
    assert result.duplicate_count == 0
    assert result.unexpected_count == 0
    assert result.identity_mismatch_count == 0
    assert result.evaluable_artifact_count == 2


# ---------------------------------------------------------------------------
# 10: corrupt manifest → read_manifest raises RunManifestError
# ---------------------------------------------------------------------------


def test_coverage_audit_corrupt_manifest_treated_as_missing(
    tmp_path: Path,
) -> None:
    """A corrupt manifest file raises RunManifestError from read_manifest.

    The aggregate's coverage audit then falls back to manifest=None
    (verified here by directly calling validate_manifest_coverage with
    None). The aggregate's error-handling path in
    ``run_reader_record_ask_eval.aggregate`` wraps read_manifest in a
    try/except RunManifestError and proceeds with manifest=None.
    """
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-corrupt")
    path = layout.manifest_path
    # Write corrupt JSON — looks like a manifest file but unparseable.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    # read_manifest MUST raise RunManifestError, NOT silently return None.
    with pytest.raises(RunManifestError) as exc_info:
        read_manifest(path)
    assert exc_info.value.reason == "corrupt_manifest"
    assert exc_info.value.path == str(path)

    # The aggregate's error-handling path then falls back to
    # manifest=None. Here we directly verify validate_manifest_coverage
    # accepts None and produces the "no manifest" CoverageAuditResult.
    arts = [
        _make_artifact(case_id="c1", run_index=0),
        _make_artifact(case_id="c1", run_index=1),
    ]
    result = validate_manifest_coverage(None, arts)
    assert result.manifest_present is False
    assert result.manifest_status is None
    assert result.evaluable_artifact_count == 2


# ===========================================================================
# Adversarial tests — single-artifact fake coverage,
# three-state manifest classification, and absent+artifacts gap.
# ===========================================================================


# ---------------------------------------------------------------------------
# Adversarial 14: single artifact MUST NOT satisfy duplicate-index coverage
# ---------------------------------------------------------------------------


def test_coverage_audit_single_artifact_does_not_satisfy_duplicate_index_fake_coverage() -> (
    None
):
    """Minimal reproduction at the coverage-audit level.

    Scenario: a hand-constructed (corrupt) manifest claims
    planned={"c":[0,0]}, completed={"c":[0,0]} (duplicate indices).
    Only ONE artifact exists on disk for (c, 0).

    Before the fix: ``is_complete()`` returned True (set() silently
    deduped [0,0] → {0}), planned_count=2, completed_count=2,
    missing_count=0, duplicate_count=0, evaluable_artifact_count=1.
    A single artifact masqueraded as two completed repetitions and
    could pass the aggregate coverage gate.

    After the fix:
    - ``from_json`` rejects this manifest entirely (duplicate within
      list — see test_from_json_rejects_planned_with_duplicate_indices).
    - For an in-memory dataclass that bypassed from_json,
      ``is_complete()`` returns False (defense-in-depth).
    - ``validate_manifest_coverage`` reports
      ``evaluable_artifact_count=1`` and ``planned_count=2``, so the
      ``evaluable_artifact_count == planned_count`` check in
      ``coverage_ok`` fails.
    - The aggregate verdict falls to ``blocked_incomplete_real_model_run``.

    This test asserts the coverage-audit level: a single artifact
    CANNOT satisfy duplicate-index fake coverage. The manifest's
    ``is_complete()`` is False, and ``evaluable_artifact_count`` (1)
    does not equal ``planned_count`` (2).
    """
    # Hand-construct the corrupt manifest (dataclass constructor does
    # NOT validate — from_json would reject this, but is_complete is
    # the defense-in-depth gate).
    manifest = ReaderRecordAskRunManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id="phase1-minrepro",
        phase=1,
        dataset_id=_DS_ID,
        dataset_schema_version=_DS_SCHEMA_VERSION,
        dataset_content_sha256=_DS_SHA,
        status="completed",
        planned_run_indices={"c": [0, 0]},  # duplicate
        completed_run_indices={"c": [0, 0]},  # duplicate
        remaining_run_indices={},
        executed_requests=1,
        executed_tokens=10,
        stop_reason=None,
    )
    # is_complete MUST be False — defense-in-depth at the dataclass level.
    assert manifest.is_complete() is False

    # Coverage audit: only ONE artifact on disk for (c, 0).
    arts = [_make_artifact(case_id="c", run_index=0)]
    result = validate_manifest_coverage(manifest, arts)

    # The audit reports planned_count=2 (counts both duplicate indices)
    # but evaluable_artifact_count=1 (only one artifact on disk).
    assert result.planned_count == 2
    assert result.completed_count == 2
    assert result.evaluable_artifact_count == 1
    # The aggregate's ``coverage_ok`` gate requires
    # ``evaluable_artifact_count == planned_count`` — that fails here,
    # so the verdict falls to ``blocked_incomplete_real_model_run``
    # via the production ``_decide_final_verdict`` (precedence 6).
    assert result.evaluable_artifact_count != result.planned_count


# ---------------------------------------------------------------------------
# Adversarial 14b: three-state classification — absent / valid / corrupt
# ---------------------------------------------------------------------------


def test_coverage_audit_three_state_classification_absent(tmp_path: Path) -> None:
    """Manifest three-state: file does not exist → ManifestState.ABSENT.

    ``read_manifest_with_state`` returns state=ABSENT, manifest=None.
    This is the "never ran" path — distinct from corrupt.
    """
    from claread_eval.reader_record_ask.run_manifest import (
        ManifestState,
        read_manifest_with_state,
    )

    path = tmp_path / "absent.json"
    result = read_manifest_with_state(path)
    assert result.state is ManifestState.ABSENT
    assert result.manifest is None


def test_coverage_audit_three_state_classification_valid(tmp_path: Path) -> None:
    """Manifest three-state: file exists and passes the strict contract →
    ManifestState.VALID with the parsed manifest.
    """
    from claread_eval.reader_record_ask.run_manifest import (
        ManifestState,
        read_manifest_with_state,
    )

    manifest = _make_manifest(status="completed")
    path = tmp_path / "manifest.json"
    write_manifest_atomic(manifest, path)

    result = read_manifest_with_state(path)
    assert result.state is ManifestState.VALID
    assert result.manifest is not None
    assert result.manifest == manifest


@pytest.mark.parametrize(
    "corrupt_content",
    [
        "{not valid json",
        '{"truncated":',
        "",
        "null",
        "[]",  # not a dict
        '{"schema_version": "wrong"}',  # missing required fields
    ],
)
def test_coverage_audit_three_state_classification_corrupt(
    tmp_path: Path, corrupt_content: str
) -> None:
    """Manifest three-state: file exists but unparseable / missing fields /
    wrong types → ManifestState.CORRUPT.

    corrupt MUST NOT be folded into absent — a corrupt manifest
    indicates the run started but its audit trail is broken.
    """
    from claread_eval.reader_record_ask.run_manifest import (
        ManifestState,
        read_manifest_with_state,
    )

    path = tmp_path / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(corrupt_content, encoding="utf-8")

    result = read_manifest_with_state(path)
    assert result.state is ManifestState.CORRUPT
    assert result.manifest is None


def test_coverage_audit_corrupt_manifest_does_not_leak_content_or_exception(
    tmp_path: Path,
) -> None:
    """Corrupt-manifest safety: ManifestReadResult carries
    ONLY the state (CORRUPT) — no file content, no exception text, no
    local sensitive information. The aggregate's report MUST NOT
    surface the corrupt content.
    """
    from claread_eval.reader_record_ask.run_manifest import (
        ManifestState,
        read_manifest_with_state,
    )

    path = tmp_path / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Embed fake "sensitive" content to prove it doesn't leak.
    path.write_text(
        '{"api_key": "sk-FAKE-DO-NOT-LEAK", "prompt": "secret"}',
        encoding="utf-8",
    )

    result = read_manifest_with_state(path)
    assert result.state is ManifestState.CORRUPT
    # ManifestReadResult is a frozen dataclass with only `state` and
    # `manifest` fields — no error/text field. So nothing leaks by
    # construction. Assert the manifest field is None.
    assert result.manifest is None
    # And the dataclass has NO field that could carry content.
    field_names = {f.name for f in result.__dataclass_fields__.values()}
    assert field_names == {"state", "manifest"}
