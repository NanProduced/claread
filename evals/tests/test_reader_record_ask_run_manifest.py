"""Tests for ReaderRecordAskRunManifest (R4-A3 final closure Task 1).

Spec: `.trae/specs/audit-r4-a3-eval-harness-final-closure/spec.md`
Requirement: Run Manifest Persistence + Aggregate Coverage Audit.

Covers:
- Manifest construction + JSON round-trip for both statuses.
- ``is_complete()`` true/false branches.
- planned / completed / remaining counts + identity_tuple.
- Atomic write (creates file, overwrites, no tmp residue).
- ``read_manifest`` fail-closed on missing / corrupt / truncated /
  missing-field / wrong-type manifests.
- Serialized manifest excludes ``answer`` / ``reasoning`` / ``article``
  / ``api_key`` / ``prompt`` / ``exception`` keywords.
- ``validate_manifest_coverage`` for: identity mismatch, complete match,
  missing artifact, duplicate, unexpected, partial + budget manifest,
  no-manifest + artifacts, no-manifest + no-artifacts.
- Invariant: ``planned == completed ∪ remaining`` per case is enforced
  in ``from_json``.
- Integration: writer + reader share ``RunSessionLayout.manifest_path``.

No real LLM / provider calls. Artifacts use ``SimpleNamespace`` mocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from claread_eval.reader_record_ask.run_manifest import (
    MANIFEST_SCHEMA_VERSION,
    ReaderRecordAskRunManifest,
    RunManifestError,
    read_manifest,
    validate_manifest_coverage,
    write_manifest_atomic,
)
from claread_eval.reader_record_ask.session import RunSessionLayout

# ---------------------------------------------------------------------------
# Helpers
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
# 1-2: JSON round-trip for both statuses
# ---------------------------------------------------------------------------


def test_manifest_completed_status_construct_and_json_roundtrip() -> None:
    manifest = _make_manifest(
        status="completed",
        planned={"c1": [0, 1], "c2": [0]},
        completed={"c1": [0, 1], "c2": [0]},
        remaining={},
        executed_requests=3,
        executed_tokens=1200,
        stop_reason=None,
    )
    s = manifest.to_json()
    back = ReaderRecordAskRunManifest.from_json(s)
    assert back == manifest


def test_manifest_budget_exhausted_status_construct_and_json_roundtrip() -> None:
    manifest = _make_manifest(
        status="budget_exhausted",
        planned={"c1": [0, 1], "c2": [0, 1]},
        completed={"c1": [0]},
        remaining={"c1": [1], "c2": [0, 1]},
        executed_requests=1,
        executed_tokens=400,
        stop_reason="budget_exhausted",
    )
    s = manifest.to_json()
    back = ReaderRecordAskRunManifest.from_json(s)
    assert back == manifest


# ---------------------------------------------------------------------------
# 3-5: is_complete()
# ---------------------------------------------------------------------------


def test_manifest_is_complete_true_for_completed_full_coverage() -> None:
    manifest = _make_manifest(
        status="completed",
        planned={"c1": [0, 1], "c2": [0, 1]},
        completed={"c1": [0, 1], "c2": [0, 1]},
        remaining={},
    )
    assert manifest.is_complete() is True


def test_manifest_is_complete_false_for_budget_exhausted() -> None:
    manifest = _make_manifest(
        status="budget_exhausted",
        planned={"c1": [0, 1]},
        completed={"c1": [0]},
        remaining={"c1": [1]},
        stop_reason="budget_exhausted",
    )
    assert manifest.is_complete() is False


def test_manifest_is_complete_false_for_completed_but_coverage_gap() -> None:
    # status=completed but planned != completed → not complete.
    manifest = _make_manifest(
        status="completed",
        planned={"c1": [0, 1], "c2": [0]},
        completed={"c1": [0, 1]},
        remaining={},
        stop_reason=None,
    )
    assert manifest.is_complete() is False


# ---------------------------------------------------------------------------
# 6: counts
# ---------------------------------------------------------------------------


def test_manifest_planned_completed_remaining_counts() -> None:
    manifest = _make_manifest(
        status="budget_exhausted",
        planned={"c1": [0, 1], "c2": [0, 1], "c3": [0, 1]},
        completed={"c1": [0, 1], "c2": [0, 1]},
        remaining={"c3": [0, 1]},
    )
    assert manifest.planned_count == 6
    assert manifest.completed_count == 4
    assert manifest.remaining_count == 2


# ---------------------------------------------------------------------------
# 7: identity_tuple
# ---------------------------------------------------------------------------


def test_manifest_identity_tuple() -> None:
    manifest = _make_manifest(
        dataset_id="ds-xyz",
        dataset_schema_version="v2",
        dataset_content_sha256="deadbeef",
    )
    assert manifest.identity_tuple() == ("ds-xyz", "v2", "deadbeef")


# ---------------------------------------------------------------------------
# 8: stop_reason allowlist
# ---------------------------------------------------------------------------


def test_manifest_stop_reason_allowlisted_only() -> None:
    # completed → None
    completed = _make_manifest(status="completed", stop_reason=None)
    assert completed.stop_reason is None
    parsed_completed = json.loads(completed.to_json())
    assert parsed_completed["stop_reason"] in (None, "budget_exhausted")

    # budget_exhausted → "budget_exhausted"
    budget = _make_manifest(status="budget_exhausted")
    assert budget.stop_reason == "budget_exhausted"
    parsed_budget = json.loads(budget.to_json())
    assert parsed_budget["stop_reason"] in (None, "budget_exhausted")


# ---------------------------------------------------------------------------
# 9-11: write_manifest_atomic
# ---------------------------------------------------------------------------


def test_write_manifest_atomic_creates_file_and_readback(tmp_path: Path) -> None:
    manifest = _make_manifest(status="completed")
    path = tmp_path / "manifest.json"
    assert not path.exists()
    write_manifest_atomic(manifest, path)
    assert path.is_file()
    back = read_manifest(path)
    assert back is not None
    assert back == manifest


def test_write_manifest_atomic_overwrite_existing(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    first = _make_manifest(
        status="budget_exhausted",
        planned={"c1": [0, 1]},
        completed={"c1": [0]},
        remaining={"c1": [1]},
        stop_reason="budget_exhausted",
        executed_requests=1,
        executed_tokens=100,
    )
    write_manifest_atomic(first, path)
    # Now write a different manifest in its place.
    second = _make_manifest(
        status="completed",
        planned={"c1": [0, 1]},
        completed={"c1": [0, 1]},
        remaining={},
        stop_reason=None,
        executed_requests=2,
        executed_tokens=200,
    )
    write_manifest_atomic(second, path)
    back = read_manifest(path)
    assert back is not None
    assert back.status == "completed"
    assert back.executed_tokens == 200
    assert back == second


def test_write_manifest_atomic_no_temp_file_left(tmp_path: Path) -> None:
    manifest = _make_manifest(status="completed")
    path = tmp_path / "manifest.json"
    write_manifest_atomic(manifest, path)
    # No .tmp residue in the parent directory.
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
    # And exactly one manifest file.
    files = [p.name for p in tmp_path.iterdir()]
    assert files == ["manifest.json"]


# ---------------------------------------------------------------------------
# 12-16: read_manifest fail-closed
# ---------------------------------------------------------------------------


def test_read_manifest_returns_none_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.json"
    assert read_manifest(path) is None


def test_read_manifest_raises_on_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RunManifestError) as exc_info:
        read_manifest(path)
    assert exc_info.value.reason == "corrupt_manifest"
    assert exc_info.value.path == str(path)


def test_read_manifest_raises_on_truncated_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    full = _make_manifest(status="completed").to_json()
    # Drop the last 20 chars — truncation breaks JSON parsing.
    path.write_text(full[:-20], encoding="utf-8")
    with pytest.raises(RunManifestError) as exc_info:
        read_manifest(path)
    assert exc_info.value.reason == "corrupt_manifest"
    assert exc_info.value.path == str(path)


def test_read_manifest_raises_on_missing_required_field(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    manifest = _make_manifest(status="completed")
    data = json.loads(manifest.to_json())
    del data["run_id"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RunManifestError) as exc_info:
        read_manifest(path)
    assert exc_info.value.reason == "corrupt_manifest"
    assert exc_info.value.path == str(path)


def test_read_manifest_raises_on_wrong_field_type(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    manifest = _make_manifest(status="completed")
    data = json.loads(manifest.to_json())
    data["phase"] = "1"  # str instead of int
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RunManifestError) as exc_info:
        read_manifest(path)
    assert exc_info.value.reason == "corrupt_manifest"
    assert exc_info.value.path == str(path)


# ---------------------------------------------------------------------------
# 17: no sensitive keywords in serialized JSON
# ---------------------------------------------------------------------------


def test_manifest_does_not_contain_answer_reasoning_article_api_key() -> None:
    manifest = _make_manifest(
        status="budget_exhausted",
        run_id="phase1-abc",
        planned={"c1": [0, 1]},
        completed={"c1": [0]},
        remaining={"c1": [1]},
        stop_reason="budget_exhausted",
        executed_requests=1,
        executed_tokens=42,
    )
    serialized = manifest.to_json().lower()
    for forbidden in (
        "answer",
        "reasoning",
        "article",
        "api_key",
        "prompt",
        "exception",
    ):
        assert forbidden not in serialized, (
            f"serialized manifest must not contain {forbidden!r}: {serialized}"
        )


# ---------------------------------------------------------------------------
# 18: coverage — identity mismatch
# ---------------------------------------------------------------------------


def test_manifest_identity_mismatch_with_artifact() -> None:
    manifest = _make_manifest(
        dataset_id="ds-a",
        dataset_schema_version="v1",
        dataset_content_sha256="a" * 64,
    )
    art = _make_artifact(
        case_id="c1",
        run_index=0,
        dataset_id="ds-B",  # mismatch
        dataset_schema_version="v1",
        dataset_content_sha256="a" * 64,
    )
    result = validate_manifest_coverage(manifest, [art])
    assert result.identity_mismatch_count == 1
    assert result.evaluable_artifact_count == 0
    assert result.dataset_identity == ("ds-a", "v1", "a" * 64)


# ---------------------------------------------------------------------------
# 19: coverage — complete match
# ---------------------------------------------------------------------------


def test_manifest_coverage_complete_planned_matches_artifacts() -> None:
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
    assert result.manifest_present is True
    assert result.manifest_status == "completed"
    assert result.planned_count == 4
    assert result.completed_count == 4
    assert result.missing_count == 0
    assert result.duplicate_count == 0
    assert result.unexpected_count == 0
    assert result.identity_mismatch_count == 0
    assert result.evaluable_artifact_count == 4
    assert result.missing_run_indices == {}
    assert result.duplicate_run_indices == {}
    assert result.unexpected_run_indices == {}


# ---------------------------------------------------------------------------
# 20: coverage — missing artifact
# ---------------------------------------------------------------------------


def test_manifest_coverage_missing_artifact() -> None:
    # Manifest claims 4 completed (c1[0,1], c2[0,1]) but disk has only 3
    # artifacts — c2[1] is missing. The manifest itself is well-formed
    # (planned == completed ∪ remaining for status=completed); the gap
    # is between manifest.completed and on-disk artifacts.
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
        # c2[1] missing from disk
    ]
    result = validate_manifest_coverage(manifest, arts)
    assert result.missing_count == 1
    assert result.missing_run_indices == {"c2": [1]}
    assert result.evaluable_artifact_count == 3
    assert result.duplicate_count == 0
    assert result.unexpected_count == 0


# ---------------------------------------------------------------------------
# 21: coverage — duplicate artifact
# ---------------------------------------------------------------------------


def test_manifest_coverage_duplicate_artifact() -> None:
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
    assert result.unexpected_count == 0
    assert result.missing_count == 0


# ---------------------------------------------------------------------------
# 22: coverage — unexpected artifact
# ---------------------------------------------------------------------------


def test_manifest_coverage_unexpected_artifact() -> None:
    manifest = _make_manifest(
        status="completed",
        planned={"c1": [0, 1]},
        completed={"c1": [0, 1]},
        remaining={},
    )
    arts = [
        _make_artifact(case_id="c1", run_index=0),
        _make_artifact(case_id="c1", run_index=1),
        _make_artifact(case_id="c2", run_index=0),  # not planned
    ]
    result = validate_manifest_coverage(manifest, arts)
    assert result.unexpected_count == 1
    assert result.unexpected_run_indices == {"c2": [0]}
    assert result.evaluable_artifact_count == 2  # 3 - 1 unexpected
    assert result.duplicate_count == 0
    assert result.missing_count == 0


# ---------------------------------------------------------------------------
# 23: coverage — partial artifacts + budget exhausted manifest
# ---------------------------------------------------------------------------


def test_manifest_coverage_partial_artifacts_with_budget_manifest() -> None:
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
    assert result.manifest_status == "budget_exhausted"
    assert result.planned_count == 4
    assert result.completed_count == 2
    assert result.missing_count == 2
    assert result.missing_run_indices == {"c2": [0, 1]}
    assert result.evaluable_artifact_count == 2
    assert result.identity_mismatch_count == 0


# ---------------------------------------------------------------------------
# 24: coverage — no manifest + artifacts
# ---------------------------------------------------------------------------


def test_manifest_coverage_no_manifest_with_artifacts() -> None:
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
    assert result.missing_run_indices == {}
    assert result.duplicate_run_indices == {}
    assert result.unexpected_run_indices == {}


# ---------------------------------------------------------------------------
# 25: coverage — no manifest + no artifacts
# ---------------------------------------------------------------------------


def test_manifest_coverage_no_manifest_no_artifacts() -> None:
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
# 26: invariant — planned == completed ∪ remaining per case (in from_json)
# ---------------------------------------------------------------------------


def test_manifest_planned_completed_remaining_coverage_invariant() -> None:
    # Construct a manifest where planned != completed + remaining.
    # The dataclass constructor does not validate, so we can build the
    # violating state, serialize it, and confirm from_json rejects it.
    manifest = ReaderRecordAskRunManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id="phase1-bad",
        phase=1,
        dataset_id=_DS_ID,
        dataset_schema_version=_DS_SCHEMA_VERSION,
        dataset_content_sha256=_DS_SHA,
        status="budget_exhausted",
        planned_run_indices={"c1": [0, 1]},
        completed_run_indices={"c1": [0]},
        remaining_run_indices={"c1": []},  # missing index 1 — invariant violated
        executed_requests=1,
        executed_tokens=100,
        stop_reason="budget_exhausted",
    )
    json_str = manifest.to_json()
    with pytest.raises(RunManifestError) as exc_info:
        ReaderRecordAskRunManifest.from_json(json_str)
    assert exc_info.value.reason == "corrupt_manifest"

    # And a manifest where completed ∩ remaining ≠ ∅ also fails.
    manifest_overlap = ReaderRecordAskRunManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id="phase1-overlap",
        phase=1,
        dataset_id=_DS_ID,
        dataset_schema_version=_DS_SCHEMA_VERSION,
        dataset_content_sha256=_DS_SHA,
        status="budget_exhausted",
        planned_run_indices={"c1": [0, 1]},
        completed_run_indices={"c1": [0, 1]},
        remaining_run_indices={"c1": [1]},  # overlap with completed
        executed_requests=1,
        executed_tokens=100,
        stop_reason="budget_exhausted",
    )
    with pytest.raises(RunManifestError):
        ReaderRecordAskRunManifest.from_json(manifest_overlap.to_json())


# ---------------------------------------------------------------------------
# 27: integration — writer + reader via RunSessionLayout.manifest_path
# ---------------------------------------------------------------------------


def test_manifest_writer_uses_run_session_layout_manifest_path(
    tmp_path: Path,
) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-int")
    manifest = _make_manifest(
        status="completed",
        run_id="phase1-int",
        planned={"c1": [0, 1]},
        completed={"c1": [0, 1]},
        remaining={},
    )
    # Layout does not create directories on its own; writer is responsible.
    path = layout.manifest_path
    assert path == tmp_path / "phase1-int" / "manifest.json"
    write_manifest_atomic(manifest, path)
    # Reader uses the same resolver — single source of truth.
    back = read_manifest(layout.manifest_path)
    assert back is not None
    assert back == manifest
    assert back.is_complete() is True


# ===========================================================================
# P0-1 adversarial tests — strict from_json rejection rules + writer
# pre-write validation. Each test asserts a specific rejection rule from
# the 10-rule P0-1 contract. Tests MUST NOT use set() to silently dedupe
# before validation — list uniqueness is verified upstream of any set
# operation in production code, and these tests pin that contract.
# ===========================================================================


def _make_valid_manifest_dict() -> dict:
    """Return a fully-valid manifest dict for adversarial mutation tests.

    Each adversarial test copies this dict, mutates a single field to
    introduce the violation under test, then asserts from_json rejects
    it. This isolates the rejection rule under test.
    """
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": "phase1-adv",
        "phase": 1,
        "dataset_id": _DS_ID,
        "dataset_schema_version": _DS_SCHEMA_VERSION,
        "dataset_content_sha256": _DS_SHA,
        "status": "completed",
        "planned_run_indices": {"c1": [0, 1]},
        "completed_run_indices": {"c1": [0, 1]},
        "remaining_run_indices": {},
        "executed_requests": 0,
        "executed_tokens": 0,
        "stop_reason": None,
    }


def _expect_reject(data: dict) -> None:
    """Serialize ``data`` and assert from_json rejects it as corrupt."""
    s = json.dumps(data)
    with pytest.raises(RunManifestError) as exc_info:
        ReaderRecordAskRunManifest.from_json(s)
    assert exc_info.value.reason == "corrupt_manifest"


# ---------------------------------------------------------------------------
# Adversarial 1: planned [0,0] rejected (duplicate within list)
# ---------------------------------------------------------------------------


def test_from_json_rejects_planned_with_duplicate_indices() -> None:
    """Rule 8: duplicate run index within a single case's planned list
    MUST be rejected. This is the KEY fix for the minimal reproduction
    bug (planned=[0,0], completed=[0,0] → is_complete=True). The
    previous implementation used set() to silently dedupe, allowing
    fake coverage.
    """
    data = _make_valid_manifest_dict()
    data["planned_run_indices"] = {"c1": [0, 0]}
    data["completed_run_indices"] = {"c1": [0, 0]}
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 2: completed [0,0] rejected (duplicate within list)
# ---------------------------------------------------------------------------


def test_from_json_rejects_completed_with_duplicate_indices() -> None:
    """Rule 8: duplicate run index within a single case's completed
    list MUST be rejected, even when planned has no duplicates.
    """
    data = _make_valid_manifest_dict()
    data["planned_run_indices"] = {"c1": [0, 1]}
    data["completed_run_indices"] = {"c1": [0, 0]}
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 3: remaining [1,1] rejected (duplicate within list)
# ---------------------------------------------------------------------------


def test_from_json_rejects_remaining_with_duplicate_indices() -> None:
    """Rule 8: duplicate run index within a single case's remaining
    list MUST be rejected. For status=budget_exhausted, remaining is
    meaningful — duplicates would let a corrupt manifest understate
    the unfinished work.
    """
    data = _make_valid_manifest_dict()
    data["status"] = "budget_exhausted"
    data["planned_run_indices"] = {"c1": [0, 1]}
    data["completed_run_indices"] = {"c1": [0]}
    data["remaining_run_indices"] = {"c1": [1, 1]}
    data["stop_reason"] = "budget_exhausted"
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 4: negative run index rejected
# ---------------------------------------------------------------------------


def test_from_json_rejects_negative_run_index() -> None:
    """Rule 8: negative run index MUST be rejected. A negative index
    could collide with sentinel values or be silently coerced by
    downstream code.
    """
    data = _make_valid_manifest_dict()
    data["planned_run_indices"] = {"c1": [-1, 0]}
    data["completed_run_indices"] = {"c1": [-1, 0]}
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 5: empty case id rejected
# ---------------------------------------------------------------------------


def test_from_json_rejects_empty_case_id() -> None:
    """Rule 8: empty-string case id MUST be rejected. An empty case_id
    could collide with default-dict lookups or hide missing data.
    """
    data = _make_valid_manifest_dict()
    data["planned_run_indices"] = {"": [0]}
    data["completed_run_indices"] = {"": [0]}
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 6: phase 0 / 4 / bool rejected (out-of-range or wrong type)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_phase", [0, 4, -1, True, False, "1", 1.5])
def test_from_json_rejects_invalid_phase_values(bad_phase) -> None:
    """Rule 4: phase MUST be an int (not bool) in {1, 2, 3}.
    bool is rejected because ``isinstance(True, int)`` is True in
    Python — the check must explicitly exclude bool.
    """
    data = _make_valid_manifest_dict()
    data["phase"] = bad_phase
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 7: negative usage counters rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("executed_requests", -1),
        ("executed_tokens", -1),
        ("executed_requests", True),  # bool rejected
        ("executed_tokens", False),  # bool rejected
    ],
)
def test_from_json_rejects_negative_or_bool_usage_counters(
    field, bad_value
) -> None:
    """Rule 9: executed_requests / executed_tokens MUST be non-negative
    ints (bool rejected). Negative counters could hide budget overflow;
    bool could be coerced to 0/1 and mislead telemetry.
    """
    data = _make_valid_manifest_dict()
    data[field] = bad_value
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 8: status="completed" + non-empty remaining rejected
# ---------------------------------------------------------------------------


def test_from_json_rejects_completed_with_nonempty_remaining() -> None:
    """Rule 7 (semantic): status="completed" MUST have empty remaining.
    A completed run with non-empty remaining is internally
    contradictory and indicates a hand-edited or partially-written
    manifest.
    """
    data = _make_valid_manifest_dict()
    data["status"] = "completed"
    data["planned_run_indices"] = {"c1": [0, 1]}
    data["completed_run_indices"] = {"c1": [0, 1]}
    data["remaining_run_indices"] = {"c1": [1]}  # non-empty
    data["stop_reason"] = None
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 9: status="completed" + budget stop_reason rejected
# ---------------------------------------------------------------------------


def test_from_json_rejects_completed_with_budget_stop_reason() -> None:
    """Rule 7 (semantic): status="completed" MUST have stop_reason=None.
    A completed run with a budget_exhausted stop_reason is contradictory.
    """
    data = _make_valid_manifest_dict()
    data["status"] = "completed"
    data["stop_reason"] = "budget_exhausted"  # contradiction
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 10: status="budget_exhausted" + None stop_reason rejected
# ---------------------------------------------------------------------------


def test_from_json_rejects_budget_exhausted_with_none_stop_reason() -> None:
    """Rule 7 (semantic): status="budget_exhausted" MUST have
    stop_reason="budget_exhausted". A budget stop without a stop_reason
    loses the audit trail for why the run stopped.
    """
    data = _make_valid_manifest_dict()
    data["status"] = "budget_exhausted"
    data["planned_run_indices"] = {"c1": [0, 1]}
    data["completed_run_indices"] = {"c1": [0]}
    data["remaining_run_indices"] = {"c1": [1]}
    data["stop_reason"] = None  # contradiction
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 11: status="budget_exhausted" + empty remaining rejected
# ---------------------------------------------------------------------------


def test_from_json_rejects_budget_exhausted_with_empty_remaining() -> None:
    """Rule 7 (semantic): status="budget_exhausted" MUST have non-empty
    remaining. A budget stop with no remaining work is contradictory —
    the run actually completed but was mislabeled.
    """
    data = _make_valid_manifest_dict()
    data["status"] = "budget_exhausted"
    data["planned_run_indices"] = {"c1": [0, 1]}
    data["completed_run_indices"] = {"c1": [0, 1]}
    data["remaining_run_indices"] = {}  # empty — contradiction
    data["stop_reason"] = "budget_exhausted"
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 12: malformed SHA-256 rejected (length / case / non-hex)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_sha",
    [
        "deadbeef",  # too short (8 chars)
        "a" * 63,  # one char short
        "a" * 65,  # one char long
        "A" * 64,  # uppercase hex rejected
        "g" * 64,  # non-hex char
        "",  # empty
        12345,  # wrong type
        None,  # wrong type
    ],
)
def test_from_json_rejects_malformed_sha256(bad_sha) -> None:
    """Rule 6: dataset_content_sha256 MUST be exactly 64 lowercase hex
    chars. Uppercase hex, wrong length, non-hex chars, and wrong types
    are all rejected. This pins the dataset identity contract.
    """
    data = _make_valid_manifest_dict()
    data["dataset_content_sha256"] = bad_sha
    _expect_reject(data)


# ---------------------------------------------------------------------------
# Adversarial 13: writer rejects hand-constructed invalid frozen dataclass
# ---------------------------------------------------------------------------


def test_write_manifest_atomic_rejects_hand_constructed_invalid_dataclass(
    tmp_path: Path,
) -> None:
    """Pre-write validation: a hand-constructed
    ``ReaderRecordAskRunManifest`` carrying duplicate indices (which
    ``from_json`` would reject) MUST NOT successfully land on disk via
    ``write_manifest_atomic``. The writer calls ``from_json(to_json())``
    BEFORE the atomic replace, so the strict contract is enforced at
    the write boundary regardless of how the dataclass was constructed.

    This is the writer-side defense for the minimal reproduction bug:
    an in-memory dataclass with planned=[0,0] / completed=[0,0] would
    pass the dataclass constructor (no validation there) but MUST NOT
    pass the writer.
    """
    # Hand-construct a dataclass with duplicate indices — the dataclass
    # constructor does NOT validate, so this succeeds.
    invalid_manifest = ReaderRecordAskRunManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id="phase1-bad",
        phase=1,
        dataset_id=_DS_ID,
        dataset_schema_version=_DS_SCHEMA_VERSION,
        dataset_content_sha256=_DS_SHA,
        status="completed",
        planned_run_indices={"c1": [0, 0]},  # duplicate
        completed_run_indices={"c1": [0, 0]},  # duplicate
        remaining_run_indices={},
        executed_requests=1,
        executed_tokens=10,
        stop_reason=None,
    )
    path = tmp_path / "manifest.json"
    with pytest.raises(RunManifestError):
        write_manifest_atomic(invalid_manifest, path)
    # The invalid manifest MUST NOT land on disk.
    assert not path.exists()
    # And no temp residue.
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Adversarial 13b: minimal reproduction — is_complete catches dup indices
# ---------------------------------------------------------------------------


def test_is_complete_rejects_minimal_reproduction_duplicate_indices() -> None:
    """Minimal reproduction: planned={"c":[0,0]}, completed={"c":[0,0]},
    artifacts=[("c",0)].

    Before the fix: ``is_complete()`` returned True (set() silently
    deduped [0,0] → {0}), planned_count=2, completed_count=2,
    missing_count=0, duplicate_count=0, evaluable_artifact_count=1.
    A single artifact masqueraded as two completed repetitions.

    After the fix: ``is_complete()`` returns False because the
    in-memory dataclass carries duplicate indices. ``from_json`` also
    rejects this state — but ``is_complete`` is the defense-in-depth
    gate for in-memory dataclasses that bypassed from_json.
    """
    manifest = ReaderRecordAskRunManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id="phase1-minrepro",
        phase=1,
        dataset_id=_DS_ID,
        dataset_schema_version=_DS_SCHEMA_VERSION,
        dataset_content_sha256=_DS_SHA,
        status="completed",
        planned_run_indices={"c": [0, 0]},
        completed_run_indices={"c": [0, 0]},
        remaining_run_indices={},
        executed_requests=1,
        executed_tokens=10,
        stop_reason=None,
    )
    # is_complete MUST be False — the manifest is internally corrupt.
    assert manifest.is_complete() is False, (
        "Minimal reproduction: is_complete MUST return False for "
        "planned=[0,0] / completed=[0,0]. set() dedup must NOT mask "
        "the duplicate."
    )
    # The counts still sum len() — but the duplicate means the manifest
    # is corrupt and must not be treated as a complete run.
    assert manifest.planned_count == 2
    assert manifest.completed_count == 2
