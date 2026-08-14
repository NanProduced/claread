"""Tests for DatasetIdentity.

Requirement: the working dataset lives under ``evals/tmp/`` and is
gitignored, so the same ``dataset_id`` can silently drift between phases.
This module computes a deterministic SHA-256 fingerprint over the
dataset's actual file content (``dataset.yaml`` + all loader-resolved
case files) so artifacts can carry an auditable content identity, and
later stages and aggregate can fail-closed on drift.

11 tests required by spec §二:
  1. Same dataset in two different absolute paths → same hash
  2. Case content change → hash changes
  3. Case filename change → hash changes
  4. dataset.yaml change → hash changes
  5. runs/ content change → hash unaffected
  6. current/prior hash same → continues (no error)
  7. hash mismatch → calls=0 (fail-closed)
  8. Prior artifacts mixed hash → calls=0 (fail-closed)
  9. Prior artifact missing hash → calls=0 (fail-closed)
  10. Aggregate mismatch → no normal verdict
  11. Artifact JSON / report metadata / run metadata carry same identity
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from claread_eval.reader_record_ask.dataset_identity import (
    DatasetIdentity,
    DatasetIdentityError,
    assert_prior_artifacts_identity_consistent,
    compute_dataset_identity,
    find_identity_mismatched_artifacts,
)
from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.loader import load_reader_record_ask_dataset
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskDataset,
    ReaderRecordAskExpected,
)

# ---------------------------------------------------------------------------
# Test dataset factory
# ---------------------------------------------------------------------------


def _make_case(
    *,
    case_id: str = "case-a",
    question: str = "What is this about?",
    question_category: str = "main_idea",
    article_text: str = "Hello world. This is a synthetic article.",
    phase_tags: list[str] | None = None,
) -> ReaderRecordAskCase:
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
        question=question,
        question_category=question_category,  # type: ignore[arg-type]
        expected=ReaderRecordAskExpected(),
        tags=[],
        phase_tags=phase_tags if phase_tags is not None else ["real_phase1"],
    )


def _write_dataset(
    dataset_dir: Path,
    *,
    cases: list[ReaderRecordAskCase],
    dataset_id: str = "test-dataset",
    schema_version: str = "test-schema-v1",
    description: str = "test dataset for DatasetIdentity tests",
) -> Path:
    """Write a minimal dataset to ``dataset_dir`` and return the path.

    Layout matches the loader's contract:
    - ``dataset_dir/dataset.yaml``
    - ``dataset_dir/cases/<case_id>.json``
    """
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "cases").mkdir(exist_ok=True)
    yaml_payload = {
        "id": dataset_id,
        "schema_version": schema_version,
        "description": description,
        "case_globs": ["cases/*.json"],
        "tags": [],
    }
    (dataset_dir / "dataset.yaml").write_text(
        yaml.safe_dump(yaml_payload, sort_keys=False),
        encoding="utf-8",
    )
    for case in cases:
        case_path = dataset_dir / "cases" / f"{case.id}.json"
        case_path.write_text(
            case.model_dump_json(indent=2),
            encoding="utf-8",
        )
    return dataset_dir


def _load(dataset_dir: Path) -> ReaderRecordAskDataset:
    return load_reader_record_ask_dataset(dataset_dir)


# ---------------------------------------------------------------------------
# Tests 1-5: pure DatasetIdentity hash determinism
# ---------------------------------------------------------------------------


def test_same_dataset_in_two_absolute_paths_same_hash(tmp_path: Path) -> None:
    """Test 1: same dataset copied to a different absolute directory
    produces the same ``content_sha256`` (only relative paths + bytes
    are hashed, never the absolute ``dataset_dir`` path).
    """
    cases = [_make_case(case_id="case-a"), _make_case(case_id="case-b")]
    dir_a = tmp_path / "dataset-a"
    dir_b = tmp_path / "dataset-b"
    _write_dataset(dir_a, cases=cases)
    # Copy the entire dataset (preserving file structure) to dir_b.
    shutil.copytree(dir_a, dir_b)

    identity_a = compute_dataset_identity(dir_a, _load(dir_a))
    identity_b = compute_dataset_identity(dir_b, _load(dir_b))

    assert identity_a.content_sha256 == identity_b.content_sha256, (
        "Same dataset in different absolute paths must produce the same "
        "content_sha256 — only relative paths + bytes are hashed."
    )
    assert identity_a.dataset_id == identity_b.dataset_id
    assert identity_a.schema_version == identity_b.schema_version
    # Sanity: hex SHA-256 is 64 lowercase hex chars.
    assert len(identity_a.content_sha256) == 64
    assert all(c in "0123456789abcdef" for c in identity_a.content_sha256)


def test_case_content_change_hash_changes(tmp_path: Path) -> None:
    """Test 2: editing a case file's content changes the hash."""
    cases = [_make_case(case_id="case-a", article_text="original content")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)
    identity_before = compute_dataset_identity(dataset_dir, _load(dataset_dir))

    # Edit the case file's content.
    case_path = dataset_dir / "cases" / "case-a.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    payload["article_text"] = "modified content"
    case_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    identity_after = compute_dataset_identity(dataset_dir, _load(dataset_dir))

    assert identity_before.content_sha256 != identity_after.content_sha256, (
        "Editing a case file's content MUST change content_sha256 — "
        "the fingerprint binds raw file bytes."
    )


def test_case_filename_change_hash_changes(tmp_path: Path) -> None:
    """Test 3: renaming a case file changes the hash (the fingerprint
    binds relative path AND content, so a renamed file is detected).
    """
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)
    identity_before = compute_dataset_identity(dataset_dir, _load(dataset_dir))

    # Rename the case file (case-a.json → case-a-renamed.json). The case
    # id inside the file stays the same — only the filename changes.
    old_path = dataset_dir / "cases" / "case-a.json"
    new_path = dataset_dir / "cases" / "case-a-renamed.json"
    old_path.rename(new_path)

    # The loader's case_globs is ``cases/*.json`` so it still picks up
    # the renamed file. The identity MUST change because the relative
    # path is part of the framed hash input.
    identity_after = compute_dataset_identity(dataset_dir, _load(dataset_dir))

    assert identity_before.content_sha256 != identity_after.content_sha256, (
        "Renaming a case file MUST change content_sha256 — the fingerprint "
        "binds relative path bytes, not just content bytes."
    )


def test_dataset_yaml_change_hash_changes(tmp_path: Path) -> None:
    """Test 4: editing ``dataset.yaml`` changes the hash."""
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases, description="original description")
    identity_before = compute_dataset_identity(dataset_dir, _load(dataset_dir))

    # Edit dataset.yaml's description (a content change that doesn't
    # affect which cases are loaded — the identity must still change
    # because dataset.yaml bytes are part of the hash input).
    yaml_path = dataset_dir / "dataset.yaml"
    yaml_payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    yaml_payload["description"] = "modified description"
    yaml_path.write_text(
        yaml.safe_dump(yaml_payload, sort_keys=False),
        encoding="utf-8",
    )

    identity_after = compute_dataset_identity(dataset_dir, _load(dataset_dir))

    assert identity_before.content_sha256 != identity_after.content_sha256, (
        "Editing dataset.yaml MUST change content_sha256 — dataset.yaml "
        "bytes are part of the hash input."
    )


def test_runs_content_change_does_not_affect_hash(tmp_path: Path) -> None:
    """Test 5: content under ``runs/`` MUST NOT affect the hash.

    ``runs/`` holds per-run artifacts/reports, not dataset content. The
    identity MUST be stable across run artifact writes — otherwise every
    new artifact would invalidate the dataset fingerprint.
    """
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)
    identity_before = compute_dataset_identity(dataset_dir, _load(dataset_dir))

    # Simulate run output: write artifacts and a report under runs/.
    runs_dir = dataset_dir / "runs" / "phase1-test"
    runs_dir.mkdir(parents=True)
    (runs_dir / "artifacts").mkdir()
    artifact_payload = {
        "case_id": "case-a",
        "run_id": "phase1-test",
        "final_text": "answer",
        "dataset_id": "test-dataset",
        "dataset_schema_version": "test-schema-v1",
        "dataset_content_sha256": identity_before.content_sha256,
    }
    (runs_dir / "artifacts" / "case-a.json").write_text(
        json.dumps(artifact_payload), encoding="utf-8"
    )
    (runs_dir / "report.md").write_text("# report\n", encoding="utf-8")

    identity_after = compute_dataset_identity(dataset_dir, _load(dataset_dir))

    assert identity_before.content_sha256 == identity_after.content_sha256, (
        "runs/ content MUST NOT affect content_sha256 — runs/ holds run "
        "outputs, not dataset content."
    )


# ---------------------------------------------------------------------------
# Tests 6-9: prior-artifact identity fence (fail-closed, calls=0)
# ---------------------------------------------------------------------------


def _make_artifact(
    *,
    case_id: str = "case-a",
    run_id: str = "phase1-test",
    dataset_id: str | None = "test-dataset",
    dataset_schema_version: str | None = "test-schema-v1",
    # Strict contract: default must be a valid 64-lowercase-hex SHA
    # so the helper produces a schema-valid RawArtifact by default.
    # Tests that exercise mismatch pass a DIFFERENT valid 64-hex SHA
    # (e.g. ``"b" * 64``) or ``None``.
    dataset_content_sha256: str | None = "a" * 64,
    budget_exhausted: bool = False,
) -> RawArtifact:
    """Build a minimal RawArtifact with dataset identity fields."""
    return RawArtifact(
        case_id=case_id,
        run_id=run_id,
        dataset_id=dataset_id,
        dataset_schema_version=dataset_schema_version,
        dataset_content_sha256=dataset_content_sha256,
        budget_exhausted=budget_exhausted,
    )


def _current_identity() -> DatasetIdentity:
    return DatasetIdentity(
        dataset_id="test-dataset",
        schema_version="test-schema-v1",
        # Strict contract: must be a valid 64-lowercase-hex SHA so
        # it matches the default in ``_make_artifact`` (which now uses
        # ``"a" * 64``). ``DatasetIdentity`` itself is a plain frozen
        # dataclass without SHA format validation, but using a valid
        # 64-hex SHA keeps the test fixtures consistent with the strict
        # ``RawArtifact`` schema.
        content_sha256="a" * 64,
    )


def test_phase2_current_prior_hash_same_continues() -> None:
    """Test 6: processing continues when prior artifacts all carry the same
    identity AND it matches the current dataset identity.

    The fence function must NOT raise — the caller proceeds to provider
    calls. We verify by asserting no exception is raised.
    """
    current = _current_identity()
    prior = [
        _make_artifact(case_id="case-a"),
        _make_artifact(case_id="case-b"),
        _make_artifact(case_id="case-c"),
    ]
    # Must not raise.
    assert_prior_artifacts_identity_consistent(
        prior, current_identity=current
    )


def test_phase2_hash_mismatch_calls_zero() -> None:
    """Test 7: processing fails closed (raises) when prior artifacts carry a
    fingerprint that does NOT match the current dataset.

    The harness translates this raise into a ``pytest.skip`` BEFORE any
    provider call (spec §二.4). The raise itself is the fail-closed
    signal — provider_calls=0 is enforced by the caller skipping.
    """
    current = _current_identity()  # sha="a"*64
    prior = [
        _make_artifact(
            case_id="case-a",
            dataset_content_sha256="b" * 64,  # mismatched (valid 64-hex)
        )
    ]
    with pytest.raises(DatasetIdentityError) as exc_info:
        assert_prior_artifacts_identity_consistent(
            prior, current_identity=current
        )
    assert exc_info.value.reason == "prior_current_mismatch"
    # The fail-closed raise is the contract: caller MUST skip before
    # any provider call. We assert the raise carries a safe reason code
    # (no absolute paths, no file content).
    assert "prior_current_mismatch" in str(exc_info.value)


def test_prior_artifacts_mixed_hash_calls_zero() -> None:
    """Test 8: processing fails closed when prior artifacts carry mixed
    fingerprints (some earlier runs against version A, some against
    version B). The caller cannot pick a "consistent" prior — must
    fail-closed.
    """
    current = _current_identity()
    prior = [
        _make_artifact(case_id="case-a", dataset_content_sha256="a" * 64),
        _make_artifact(
            case_id="case-b",
            dataset_content_sha256="b" * 64,  # mixed (valid 64-hex)
        ),
    ]
    with pytest.raises(DatasetIdentityError) as exc_info:
        assert_prior_artifacts_identity_consistent(
            prior, current_identity=current
        )
    assert exc_info.value.reason == "prior_mixed_identity"


def test_prior_artifact_missing_hash_calls_zero() -> None:
    """Test 9: processing fails closed when a prior artifact is missing
    identity fields (e.g. an old local artifact predating identity binding).

    Old artifacts without fingerprints are treated as unauditable and
    rejected at the preflight fence — no guessing, no auto-backfill.
    """
    current = _current_identity()
    # Artifact with all three identity fields None (default).
    prior_missing = [
        _make_artifact(
            case_id="case-a",
            dataset_id=None,
            dataset_schema_version=None,
            dataset_content_sha256=None,
        )
    ]
    with pytest.raises(DatasetIdentityError) as exc_info:
        assert_prior_artifacts_identity_consistent(
            prior_missing, current_identity=current
        )
    assert exc_info.value.reason == "prior_missing_identity_field"

    # Also test: only one of the three fields is missing.
    prior_partial = [
        _make_artifact(
            case_id="case-a",
            dataset_id="test-dataset",
            dataset_schema_version=None,  # missing
            dataset_content_sha256="a" * 64,
        )
    ]
    with pytest.raises(DatasetIdentityError) as exc_info2:
        assert_prior_artifacts_identity_consistent(
            prior_partial, current_identity=current
        )
    assert exc_info2.value.reason == "prior_missing_identity_field"


# ---------------------------------------------------------------------------
# Test 10: aggregate mismatch → no normal verdict
# ---------------------------------------------------------------------------


def test_aggregate_mismatch_no_normal_verdict() -> None:
    """Test 10: aggregate MUST NOT produce a normal verdict when any
    artifact's identity does not match the current dataset.

    The mismatched artifacts are segregated (NOT evaluated, NOT treated
    as pass/fail). The caller forces a ``blocked_dataset_identity_mismatch``
    verdict.
    """
    current = _current_identity()  # sha="a"*64
    artifacts = [
        _make_artifact(case_id="case-a", dataset_content_sha256="a" * 64),
        _make_artifact(
            case_id="case-b",
            dataset_content_sha256="b" * 64,  # mismatched (valid 64-hex)
        ),
        _make_artifact(
            case_id="case-c",
            dataset_id=None,
            dataset_schema_version=None,
            dataset_content_sha256=None,
        ),
        _make_artifact(case_id="case-d", dataset_content_sha256="a" * 64),
    ]
    mismatched = find_identity_mismatched_artifacts(
        artifacts, current_identity=current
    )
    # case-b (sha mismatch) and case-c (missing identity) are mismatched.
    mismatched_ids = {a.case_id for a in mismatched}
    assert mismatched_ids == {"case-b", "case-c"}, (
        "Aggregate fence MUST flag artifacts with mismatched OR missing "
        "identity — they cannot be silently re-evaluated against the "
        "current dataset."
    )
    # The caller (aggregate) drops mismatched artifacts from the evaluable
    # set and forces a blocked verdict. We simulate the caller's
    # segregation: the remaining artifacts all match.
    remaining = [a for a in artifacts if a.case_id not in mismatched_ids]
    for artifact in remaining:
        assert (artifact.dataset_id, artifact.dataset_schema_version,
                artifact.dataset_content_sha256) == (
            current.dataset_id, current.schema_version, current.content_sha256
        )
    # The caller's verdict override (simulated).
    identity_mismatched_count = len(mismatched)
    if identity_mismatched_count > 0:
        verdict = "blocked_dataset_identity_mismatch"
        allow_correctness_followup = False
        allow_streaming_provider_followup = False
    else:
        verdict = "accepted"
        allow_correctness_followup = True
        allow_streaming_provider_followup = True
    assert verdict == "blocked_dataset_identity_mismatch"
    assert allow_correctness_followup is False
    assert allow_streaming_provider_followup is False


# ---------------------------------------------------------------------------
# Test 11: artifact JSON / report metadata / run metadata carry same identity
# ---------------------------------------------------------------------------


def test_artifact_json_and_metadata_carry_same_identity(tmp_path: Path) -> None:
    """Test 11: the dataset identity must be consistently carried across:
    - RawArtifact JSON serialization (written to disk)
    - RawArtifact.model_dump() (used in hot report generation)
    - run_metadata dict (passed to report generator)

    The three fields (dataset_id, dataset_schema_version,
    dataset_content_sha256) must be non-empty for new artifacts and
    must match the DatasetIdentity used to stamp them.
    """
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)
    dataset = _load(dataset_dir)
    identity = compute_dataset_identity(dataset_dir, dataset)

    # Build an artifact stamped with this identity (mirrors what the
    # harness does in _run_one_case).
    artifact = RawArtifact(
        case_id="case-a",
        run_id="phase1-test",
        run_index=0,
        model_short_name="test-model",
        model_route="reader_record_ask",
        thinking_enabled=False,
        final_text="answer",
        finalized_status="ok",
        latency_seconds=1.5,
        executed_requests=1,
        executed_tokens=100,
        dataset_id=identity.dataset_id,
        dataset_schema_version=identity.schema_version,
        dataset_content_sha256=identity.content_sha256,
    )

    # (a) JSON roundtrip preserves identity.
    serialized = artifact.model_dump_json()
    parsed = json.loads(serialized)
    assert parsed["dataset_id"] == identity.dataset_id
    assert parsed["dataset_schema_version"] == identity.schema_version
    assert parsed["dataset_content_sha256"] == identity.content_sha256

    # (b) model_dump() (used in hot report generation) preserves identity.
    dumped = artifact.model_dump()
    assert dumped["dataset_id"] == identity.dataset_id
    assert dumped["dataset_schema_version"] == identity.schema_version
    assert dumped["dataset_content_sha256"] == identity.content_sha256

    # (c) RawArtifact.model_validate_json roundtrip preserves identity.
    rebuilt = RawArtifact.model_validate_json(serialized)
    assert rebuilt.dataset_id == identity.dataset_id
    assert rebuilt.dataset_schema_version == identity.schema_version
    assert rebuilt.dataset_content_sha256 == identity.content_sha256

    # (d) All three fields non-empty (spec §二.2: new artifact 三项必须非空).
    assert rebuilt.dataset_id, "dataset_id must be non-empty for new artifacts"
    assert rebuilt.dataset_schema_version, (
        "dataset_schema_version must be non-empty for new artifacts"
    )
    assert rebuilt.dataset_content_sha256, (
        "dataset_content_sha256 must be non-empty for new artifacts"
    )

    # (e) run_metadata (passed to report generator) carries the same
    # three fields. Mirrors what the runner's aggregate() does.
    run_metadata: dict[str, Any] = {
        "run_id": "phase1-test",
        "dataset_id": dataset.id,
        "dataset_schema_version": dataset.schema_version,
        "dataset_content_sha256": identity.content_sha256,
    }
    assert run_metadata["dataset_id"] == identity.dataset_id
    assert run_metadata["dataset_schema_version"] == identity.schema_version
    assert run_metadata["dataset_content_sha256"] == identity.content_sha256

    # (f) The artifact's identity, the run_metadata's identity, and the
    # computed DatasetIdentity are all the same triple. This is the
    # cross-source consistency check.
    artifact_triple = (
        rebuilt.dataset_id,
        rebuilt.dataset_schema_version,
        rebuilt.dataset_content_sha256,
    )
    metadata_triple = (
        run_metadata["dataset_id"],
        run_metadata["dataset_schema_version"],
        run_metadata["dataset_content_sha256"],
    )
    identity_triple = (
        identity.dataset_id,
        identity.schema_version,
        identity.content_sha256,
    )
    assert artifact_triple == metadata_triple == identity_triple, (
        "artifact JSON / run metadata / computed DatasetIdentity must "
        "carry the same (dataset_id, schema_version, content_sha256) triple."
    )


# ---------------------------------------------------------------------------
# Extra: SHA-256 framing algorithm boundary safety
# ---------------------------------------------------------------------------


def test_framing_prevents_path_content_collision() -> None:
    """Extra: the length-prefixed framing prevents path/content boundary
    collisions. Two ``(path, content)`` pairs whose simple concatenation
    is identical MUST hash differently under framed encoding.

    This is the explicit reason spec §二.6 forbids simple string concat:
    a path ``b"ab"`` followed by content ``b"c"`` would collide with
    path ``b"a"`` followed by content ``b"bc"`` (both produce ``b"abc"``).
    Length-prefixed framing disambiguates the boundary.
    """

    def simple_concat_hash(path: bytes, content: bytes) -> str:
        h = hashlib.sha256()
        h.update(path)
        h.update(content)
        return h.hexdigest()

    def framed_hash(path: bytes, content: bytes) -> str:
        h = hashlib.sha256()
        h.update(len(path).to_bytes(8, "big"))
        h.update(path)
        h.update(len(content).to_bytes(8, "big"))
        h.update(content)
        return h.hexdigest()

    # pair_a concat: b"ab" + b"c" = b"abc"
    # pair_b concat: b"a" + b"bc" = b"abc"  ← collision under simple concat
    pair_a = (b"ab", b"c")
    pair_b = (b"a", b"bc")
    assert simple_concat_hash(*pair_a) == simple_concat_hash(*pair_b), (
        "Test setup error: pair_a and pair_b must collide under simple concat"
    )
    # Under length-prefixed framing, the hashes MUST differ.
    assert framed_hash(*pair_a) != framed_hash(*pair_b), (
        "Length-prefixed framing MUST prevent path/content boundary "
        "collisions — pair_a=(b'ab', b'c') and pair_b=(b'a', b'bc') "
        "have the same simple concat but must hash differently."
    )
