"""Atomic dataset snapshot single-read contract tests.

Spec: ``evals/tmp/reader-record-ask-r4-a3/review/...
TMP-reader-record-ask-r4-a3-eval-2026-07-17.md`` (snapshot rework).

Background: the prior implementation read the dataset files TWICE —
once in the legacy dataset loader (parsing YAML/JSON into Python
objects) and again in :func:`compute_dataset_identity` (re-reading the
same files to compute the SHA-256 fingerprint). Because the working
dataset lives under ``evals/tmp/`` and is gitignored, a mutation
between the two reads could desynchronize the parsed dataset from the
computed identity — the fingerprint would no longer prove what the
parser actually consumed.

The rework introduces an atomic snapshot contract:

    :class:`LoadedReaderRecordAskDatasetSnapshot`
    ─── produced by the snapshot loader ───
    captures ``dataset.yaml`` and every loader-resolved case file as
    raw bytes in a SINGLE read pass. Both the parsed
    parsed dataset model and the
    :class:`DatasetIdentity` are derived from the SAME captured bytes.

These tests prove the contract:

1. Each file (``dataset.yaml`` + every case file) is read exactly ONCE
   from disk. The loader does NOT re-read for identity computation.
2. Schema parsing and content SHA-256 are computed from the SAME bytes
   (mutation after capture cannot desync them).
3. After capture, disk mutations do NOT affect the snapshot's
   internal consistency (``snapshot.dataset`` and ``snapshot.identity``
   remain bound to the captured bytes).
4. The existing fingerprint properties (cross-path stability, content
   mutation sensitivity, runs/ exclusion) still hold when going
   through the snapshot loader.
5. The snapshot's identity matches the legacy
   :func:`compute_dataset_identity` hash for the same bytes (the
   snapshot contract does not change the hash algorithm).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from claread_eval.reader_record_ask.dataset_identity import (
    DatasetIdentity,
    compute_dataset_identity,
)
from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.loader import (
    LoadedReaderRecordAskDatasetSnapshot,
    load_reader_record_ask_dataset,
    load_reader_record_ask_dataset_with_snapshot,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskExpected,
)

# ---------------------------------------------------------------------------
# Factory helpers — mirror the layout used by the existing identity tests.
# ---------------------------------------------------------------------------


def _make_case(
    *,
    case_id: str = "case-a",
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
        question="What is this about?",
        question_category="main_idea",
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
    description: str = "synthetic test dataset",
) -> Path:
    """Write a minimal dataset matching the loader's contract."""
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
        (dataset_dir / "cases" / f"{case.id}.json").write_text(
            case.model_dump_json(indent=2),
            encoding="utf-8",
        )
    return dataset_dir


# ---------------------------------------------------------------------------
# Test 1: each file is read EXACTLY ONCE from disk.
# ---------------------------------------------------------------------------


def test_each_file_read_exactly_once(tmp_path: Path) -> None:
    """The snapshot loader reads each file exactly ONCE.

    Prior to the rework, the legacy dataset loader read files for
    parsing, then :func:`compute_dataset_identity` re-read the SAME
    files for hashing. This double-read was the root cause of the
    snapshot desync risk.

    This test patches :meth:`Path.read_bytes` to count invocations
    per file. After the snapshot loader returns:

    - ``dataset.yaml`` MUST have been read exactly once.
    - Each case file MUST have been read exactly once.

    A second read (e.g. by an identity recomputation pass) would
    fail this test.
    """
    cases = [
        _make_case(case_id="case-a"),
        _make_case(case_id="case-b"),
        _make_case(case_id="case-c"),
    ]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)

    # Track read_bytes calls per file path.
    read_counts: dict[str, int] = {}
    original_read_bytes = Path.read_bytes

    def _tracking_read_bytes(self: Path) -> bytes:
        key = str(self.resolve())
        read_counts[key] = read_counts.get(key, 0) + 1
        return original_read_bytes(self)

    # Patch read_bytes on the Path class. The loader uses
    # ``yaml_path.read_bytes()`` and ``case_path.read_bytes()``, both
    # of which dispatch through ``Path.read_bytes``.
    original_method = Path.read_bytes
    Path.read_bytes = _tracking_read_bytes  # type: ignore[method-assign]
    try:
        snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
    finally:
        Path.read_bytes = original_method  # type: ignore[method-assign]

    # The snapshot must have loaded all 3 cases + dataset.yaml.
    assert len(snapshot.dataset.cases) == 3
    assert snapshot.identity.content_sha256  # identity was computed

    # dataset.yaml MUST have been read exactly once.
    yaml_reads = [
        count
        for path, count in read_counts.items()
        if path.endswith("dataset.yaml")
    ]
    assert len(yaml_reads) == 1, (
        f"Expected exactly 1 dataset.yaml read path, got {yaml_reads}. "
        f"All reads: {read_counts}"
    )
    assert yaml_reads[0] == 1, (
        f"dataset.yaml MUST be read exactly once; got {yaml_reads[0]} reads. "
        "The snapshot loader must NOT re-read for identity computation."
    )

    # Each case file MUST have been read exactly once.
    for case in cases:
        case_reads = [
            count
            for path, count in read_counts.items()
            if path.endswith(f"cases\\{case.id}.json")
            or path.endswith(f"cases/{case.id}.json")
        ]
        assert len(case_reads) == 1, (
            f"Expected exactly 1 read path for case {case.id}, got "
            f"{case_reads}. All reads: {read_counts}"
        )
        assert case_reads[0] == 1, (
            f"Case file {case.id}.json MUST be read exactly once; got "
            f"{case_reads[0]} reads. The snapshot loader must NOT re-read "
            "for identity computation."
        )


def test_loader_does_not_call_compute_dataset_identity(tmp_path: Path) -> None:
    """The snapshot loader MUST NOT call the legacy
    :func:`compute_dataset_identity` (which re-reads files from disk).

    The snapshot loader uses :func:`compute_dataset_identity_from_bytes`
    instead — operating on already-captured bytes. Any call to the
    disk-reading legacy function would indicate a regression.
    """
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)

    # Track calls to the legacy compute_dataset_identity. We patch it
    # on the dataset_identity module (where the loader imports from).
    from claread_eval.reader_record_ask import dataset_identity as di_module

    original = di_module.compute_dataset_identity
    call_count = {"n": 0}

    def _tracking_legacy(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    di_module.compute_dataset_identity = _tracking_legacy  # type: ignore[assignment]
    try:
        snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
    finally:
        di_module.compute_dataset_identity = original  # type: ignore[assignment]

    assert call_count["n"] == 0, (
        "load_reader_record_ask_dataset_with_snapshot MUST NOT call the legacy "
        "compute_dataset_identity (which re-reads files from disk). "
        f"Got {call_count['n']} calls. Use "
        "compute_dataset_identity_from_bytes on captured bytes instead."
    )
    # Sanity: the snapshot still has an identity.
    assert snapshot.identity.content_sha256


# ---------------------------------------------------------------------------
# Test 2: parse and hash use the SAME bytes.
# ---------------------------------------------------------------------------


def test_parse_and_hash_use_same_bytes(tmp_path: Path) -> None:
    """Schema parsing and content SHA-256 are derived from the
    SAME captured bytes.

    This is the core atomic-snapshot invariant. We prove it by
    mutating the files on disk AFTER the snapshot is captured and
    asserting that:

    - The snapshot's ``dataset`` still reflects the ORIGINAL bytes
      (the parsed cases did not change).
    - The snapshot's ``identity.content_sha256`` still matches the
      hash of the ORIGINAL bytes (it did not pick up the mutation).

    If the loader re-read files for identity computation, the hash
    would reflect the mutated bytes while the parsed dataset reflected
    the original bytes — the snapshot would be inconsistent.
    """
    cases = [
        _make_case(case_id="case-a", article_text="original content A"),
        _make_case(case_id="case-b", article_text="original content B"),
    ]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)

    # Capture the snapshot.
    snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
    original_sha = snapshot.identity.content_sha256
    original_article_a = snapshot.dataset.cases[0].article_text

    # Compute the legacy hash from the SAME original bytes (re-read
    # them now, before mutation). This must match the snapshot's hash.
    legacy_identity_before = compute_dataset_identity(
        dataset_dir, snapshot.dataset
    )
    assert legacy_identity_before.content_sha256 == original_sha, (
        "Snapshot identity MUST match the legacy hash for the same bytes "
        "(the snapshot contract does not change the hash algorithm)."
    )

    # Mutate the case file on disk AFTER the snapshot was captured.
    case_path = dataset_dir / "cases" / "case-a.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    payload["article_text"] = "MUTATED content A"
    case_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # The snapshot's parsed dataset MUST still reflect the original
    # bytes — the mutation did NOT leak into the in-memory snapshot.
    assert snapshot.dataset.cases[0].article_text == "original content A", (
        "Snapshot.dataset MUST reflect the bytes captured at load time. "
        "A disk mutation after capture MUST NOT change the parsed dataset."
    )
    assert snapshot.dataset.cases[0].article_text == original_article_a

    # The snapshot's identity MUST still match the original hash —
    # NOT the mutated bytes.
    assert snapshot.identity.content_sha256 == original_sha, (
        "Snapshot.identity.content_sha256 MUST be bound to the bytes "
        "captured at load time. A disk mutation after capture MUST NOT "
        "change the identity hash."
    )

    # Re-loading the dataset from the mutated disk produces a DIFFERENT
    # hash — proving the mutation was real and the snapshot's stability
    # is not a fluke of the mutation being a no-op.
    mutated_snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
    assert mutated_snapshot.identity.content_sha256 != original_sha, (
        "After mutating the case file, a fresh snapshot MUST produce a "
        "different content_sha256. If it matches the original, the "
        "mutation was not actually persisted to disk."
    )
    assert (
        mutated_snapshot.dataset.cases[0].article_text == "MUTATED content A"
    )


def test_dataset_yaml_mutation_after_capture_does_not_affect_snapshot(
    tmp_path: Path,
) -> None:
    """Mutating ``dataset.yaml`` AFTER capture MUST NOT affect
    the snapshot.

    The previous test mutated a case file. This test mutates
    ``dataset.yaml`` itself — verifying that the YAML bytes are also
    captured exactly once and bound to both the parsed dataset
    metadata and the identity hash.
    """
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases, description="original description")

    snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
    original_sha = snapshot.identity.content_sha256

    # Mutate dataset.yaml after capture.
    yaml_path = dataset_dir / "dataset.yaml"
    yaml_payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    yaml_payload["description"] = "MUTATED description"
    yaml_path.write_text(
        yaml.safe_dump(yaml_payload, sort_keys=False),
        encoding="utf-8",
    )

    # The snapshot's identity MUST be unchanged.
    assert snapshot.identity.content_sha256 == original_sha, (
        "Mutating dataset.yaml after capture MUST NOT change the snapshot "
        "identity — the snapshot is bound to the captured bytes."
    )

    # Re-loading from the mutated disk produces a different hash.
    mutated_snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
    assert mutated_snapshot.identity.content_sha256 != original_sha, (
        "A fresh snapshot from the mutated dataset.yaml MUST produce a "
        "different content_sha256 (proves the mutation was persisted)."
    )


# ---------------------------------------------------------------------------
# Test 3: snapshot type and single-read structure.
# ---------------------------------------------------------------------------


def test_snapshot_is_frozen_dataclass(tmp_path: Path) -> None:
    """:class:`LoadedReaderRecordAskDatasetSnapshot` is a
    ``@dataclass(frozen=True)`` — the snapshot is immutable after
    capture.

    Immutability is part of the atomic-snapshot contract: once the
    byte capture is bound to the parsed dataset and identity, callers
    MUST NOT be able to mutate either field. This makes the snapshot
    safe to pass around without defensive copying.
    """
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)

    snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    # Frozen dataclass: assigning to a field raises FrozenInstanceError.
    with pytest.raises(AttributeError):
        snapshot.dataset = None  # type: ignore[misc]

    with pytest.raises(AttributeError):
        snapshot.identity = DatasetIdentity(  # type: ignore[misc]
            dataset_id="other",
            schema_version="other",
            content_sha256="0" * 64,
        )

    # The fields are themselves immutable (DatasetIdentity is frozen).
    with pytest.raises(AttributeError):
        snapshot.identity.content_sha256 = "0" * 64  # type: ignore[misc]


def test_load_dataset_returns_snapshot_dataset(tmp_path: Path) -> None:
    """The legacy dataset-loader adapter MUST reuse
    the snapshot loader internally.

    The adapter returns ``snapshot.dataset`` (discarding the identity).
    This keeps backwards compatibility without introducing a second
    load path that bypasses the snapshot contract.
    """
    cases = [_make_case(case_id="case-a"), _make_case(case_id="case-b")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)

    # Both functions must produce the same parsed dataset.
    snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
    legacy_dataset = load_reader_record_ask_dataset(dataset_dir)

    assert len(legacy_dataset.cases) == len(snapshot.dataset.cases) == 2
    assert [c.id for c in legacy_dataset.cases] == [
        c.id for c in snapshot.dataset.cases
    ]
    # Field-equal: the legacy adapter did not mutate the dataset.
    assert legacy_dataset.model_dump() == snapshot.dataset.model_dump()


# ---------------------------------------------------------------------------
# Test 4: existing fingerprint properties still hold via snapshot loader.
# ---------------------------------------------------------------------------


def test_snapshot_identity_same_for_same_dataset_in_different_paths(
    tmp_path: Path,
) -> None:
    """The snapshot identity is stable across absolute paths.

    The hash inputs are POSIX relative paths + raw bytes — the
    absolute ``dataset_dir`` is NOT part of the hash. Two copies of
    the same dataset MUST produce the same ``content_sha256`` via the
    snapshot loader.
    """
    cases = [_make_case(case_id="case-a"), _make_case(case_id="case-b")]
    dir_a = tmp_path / "dataset-a"
    dir_b = tmp_path / "dataset-b"
    _write_dataset(dir_a, cases=cases)
    shutil.copytree(dir_a, dir_b)

    snapshot_a = load_reader_record_ask_dataset_with_snapshot(dir_a)
    snapshot_b = load_reader_record_ask_dataset_with_snapshot(dir_b)

    assert snapshot_a.identity.content_sha256 == snapshot_b.identity.content_sha256, (
        "Same dataset in different absolute paths MUST produce the same "
        "content_sha256 via the snapshot loader."
    )
    assert snapshot_a.identity.dataset_id == snapshot_b.identity.dataset_id
    assert snapshot_a.identity.schema_version == snapshot_b.identity.schema_version


def test_snapshot_identity_changes_on_case_content_mutation(
    tmp_path: Path,
) -> None:
    """Editing a case file's content changes the snapshot identity."""
    cases = [_make_case(case_id="case-a", article_text="original content")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)

    before = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    # Mutate the case file content.
    case_path = dataset_dir / "cases" / "case-a.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    payload["article_text"] = "modified content"
    case_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    after = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    assert before.identity.content_sha256 != after.identity.content_sha256, (
        "Editing a case file's content MUST change the snapshot identity."
    )


def test_snapshot_identity_changes_on_filename_rename(tmp_path: Path) -> None:
    """Renaming a case file changes the snapshot identity (the
    fingerprint binds relative path AND content)."""
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)

    before = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    # Rename the case file (the case id inside stays the same).
    old_path = dataset_dir / "cases" / "case-a.json"
    new_path = dataset_dir / "cases" / "case-a-renamed.json"
    old_path.rename(new_path)

    after = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    assert before.identity.content_sha256 != after.identity.content_sha256, (
        "Renaming a case file MUST change the snapshot identity — the "
        "fingerprint binds relative path bytes."
    )


def test_snapshot_identity_changes_on_dataset_yaml_mutation(
    tmp_path: Path,
) -> None:
    """Editing ``dataset.yaml`` changes the snapshot identity."""
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases, description="original")
    before = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    # Mutate dataset.yaml.
    yaml_path = dataset_dir / "dataset.yaml"
    yaml_payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    yaml_payload["description"] = "modified"
    yaml_path.write_text(
        yaml.safe_dump(yaml_payload, sort_keys=False),
        encoding="utf-8",
    )

    after = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    assert before.identity.content_sha256 != after.identity.content_sha256, (
        "Editing dataset.yaml MUST change the snapshot identity."
    )


def test_snapshot_identity_unaffected_by_runs_content(tmp_path: Path) -> None:
    """Content under ``runs/`` MUST NOT affect the snapshot identity.

    The loader excludes ``runs/`` from both parsing and identity
    hashing — run artifacts/reports are not dataset content.
    """
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)
    before = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    # Write run artifacts under runs/.
    runs_dir = dataset_dir / "runs" / "phase1-test"
    runs_dir.mkdir(parents=True)
    (runs_dir / "artifacts").mkdir()
    artifact_payload = {
        "case_id": "case-a",
        "run_id": "phase1-test",
        "final_text": "answer",
        "dataset_id": before.identity.dataset_id,
        "dataset_schema_version": before.identity.schema_version,
        "dataset_content_sha256": before.identity.content_sha256,
    }
    (runs_dir / "artifacts" / "case-a.json").write_text(
        json.dumps(artifact_payload), encoding="utf-8"
    )
    (runs_dir / "report.md").write_text("# report\n", encoding="utf-8")

    after = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    assert before.identity.content_sha256 == after.identity.content_sha256, (
        "runs/ content MUST NOT affect the snapshot identity."
    )


# ---------------------------------------------------------------------------
# Test 5: artifact stamping uses snapshot.identity (integration).
# ---------------------------------------------------------------------------


def test_artifact_stamping_uses_snapshot_identity(tmp_path: Path) -> None:
    """An artifact stamped with ``snapshot.identity`` round-trips
    through :func:`find_identity_mismatched_artifacts` as a MATCH.

    This proves the snapshot identity is the same triple that the
    aggregate path uses for the identity fence — there is no second
    identity computation that could diverge.
    """
    from claread_eval.reader_record_ask.dataset_identity import (
        find_identity_mismatched_artifacts,
    )

    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)

    snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    # Build an artifact stamped with the snapshot's identity.
    artifact = RawArtifact(
        case_id="case-a",
        run_id="phase1-test",
        dataset_id=snapshot.identity.dataset_id,
        dataset_schema_version=snapshot.identity.schema_version,
        dataset_content_sha256=snapshot.identity.content_sha256,
    )

    # The fence MUST find zero mismatches — the artifact's identity
    # matches the snapshot's identity exactly.
    mismatched = find_identity_mismatched_artifacts(
        [artifact],
        current_identity=snapshot.identity,
    )
    assert mismatched == [], (
        "An artifact stamped with snapshot.identity MUST be a match — "
        "the snapshot is the single source of truth for the dataset "
        "fingerprint."
    )

    # Conversely: an artifact stamped with a different sha is flagged.
    bad_artifact = RawArtifact(
        case_id="case-a",
        run_id="phase1-test",
        dataset_id=snapshot.identity.dataset_id,
        dataset_schema_version=snapshot.identity.schema_version,
        dataset_content_sha256="0" * 64,  # mismatched
    )
    mismatched = find_identity_mismatched_artifacts(
        [bad_artifact],
        current_identity=snapshot.identity,
    )
    assert len(mismatched) == 1, (
        "An artifact with a mismatched sha MUST be flagged by the fence."
    )


# ---------------------------------------------------------------------------
# Test 6: documentation accuracy — the snapshot docstring MUST
# declare single-byte-capture identity-bound semantics, MUST warn that
# the snapshot is NOT a deep immutable object, and MUST instruct
# callers not to mutate ``snapshot.dataset``. These are introspection
# tests on ``__doc__`` so that the contract is enforced even if the
# module is imported lazily.
# ---------------------------------------------------------------------------


def test_snapshot_docstring_declares_single_byte_capture() -> None:
    """Docstring MUST include the phrase "single-byte-capture" and
    "identity-bound" so that callers understand the snapshot binds
    exactly the bytes captured at load time.

    Spec: ``Snapshot Documentation Accuracy`` —
    "single-byte-capture identity-bound snapshot".
    """
    doc = LoadedReaderRecordAskDatasetSnapshot.__doc__ or ""
    doc_lower = doc.lower()
    assert "single-byte-capture" in doc_lower, (
        "LoadedReaderRecordAskDatasetSnapshot docstring MUST declare "
        "'single-byte-capture'. Got doc:\n" + doc
    )
    assert "identity-bound" in doc_lower, (
        "LoadedReaderRecordAskDatasetSnapshot docstring MUST declare "
        "'identity-bound'. Got doc:\n" + doc
    )


def test_snapshot_docstring_declares_not_deep_immutable() -> None:
    """Docstring MUST explicitly state that the snapshot is NOT a deep
    immutable object.

    The frozen dataclass only prevents re-binding the top-level fields;
    the inner Pydantic dataset remains mutable. This must be documented
    so callers do not assume transitive immutability.

    Spec: ``Snapshot Documentation Accuracy`` —
    "NOT a deep immutable object; callers MUST NOT mutate ``dataset``".
    """
    doc = LoadedReaderRecordAskDatasetSnapshot.__doc__ or ""
    doc_lower = doc.lower()
    assert "not a deep immutable object" in doc_lower, (
        "LoadedReaderRecordAskDatasetSnapshot docstring MUST declare "
        "'NOT a deep immutable object'. Got doc:\n" + doc
    )


def test_snapshot_docstring_declares_callers_must_not_mutate() -> None:
    """Docstring MUST warn that callers MUST NOT mutate
    ``snapshot.dataset``.

    Spec: ``Snapshot Documentation Accuracy`` — "callers MUST NOT
    mutate ``dataset``". Equivalent wording like "禁止修改" is also
    accepted.
    """
    doc = LoadedReaderRecordAskDatasetSnapshot.__doc__ or ""
    doc_lower = doc.lower()
    assert "must not mutate" in doc_lower or "禁止修改" in doc, (
        "LoadedReaderRecordAskDatasetSnapshot docstring MUST declare "
        "'MUST NOT mutate' or '禁止修改'. Got doc:\n" + doc
    )


def test_snapshot_frozen_prevents_rebinding_but_not_internal_mutation(
    tmp_path: Path,
) -> None:
    """Behavior test: ``@dataclass(frozen=True)`` prevents re-binding
    ``snapshot.dataset`` but does NOT prevent mutation of the inner
    Pydantic model.

    This is exactly the gap the docstring warns about. The test
    verifies the actual runtime behavior so that:

    - Re-binding ``snapshot.dataset = ...`` raises ``FrozenInstanceError``
      (a subclass of ``AttributeError``).
    - ``snapshot.dataset.cases.append(...)`` succeeds silently — the
      frozen dataclass only blocks re-binding the field reference, not
      deep mutation of the referenced Pydantic model.

    Note: this test does NOT endorse mutating the snapshot — it proves
    that the frozen dataclass alone is insufficient to enforce deep
    immutability, which is precisely why the docstring MUST warn
    callers. If a future schema change makes the dataset deeply
    immutable (e.g. switching to ``MappingProxyType`` or a frozen
    Pydantic config), this test must be updated alongside the
    docstring so the two stay in sync.
    """
    cases = [_make_case(case_id="case-a")]
    dataset_dir = tmp_path / "dataset"
    _write_dataset(dataset_dir, cases=cases)

    snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)

    # Sanity: the snapshot class is still frozen at the top level.
    assert LoadedReaderRecordAskDatasetSnapshot.__dataclass_params__.frozen, (
        "LoadedReaderRecordAskDatasetSnapshot MUST remain "
        "@dataclass(frozen=True)."
    )

    # Re-binding the top-level field MUST be rejected (FrozenInstanceError
    # is a subclass of AttributeError).
    with pytest.raises(AttributeError):
        snapshot.dataset = None  # type: ignore[misc]

    # Internal mutation of the Pydantic dataset is NOT blocked by the
    # frozen dataclass — this is the gap the docstring warns about.
    new_case = _make_case(case_id="case-b", article_text="extra mutation")
    before_count = len(snapshot.dataset.cases)
    # This SHOULD NOT raise — frozen dataclass only blocks re-binding
    # the field reference, not deep mutation of the referenced object.
    snapshot.dataset.cases.append(new_case)
    assert len(snapshot.dataset.cases) == before_count + 1, (
        "Appending to snapshot.dataset.cases MUST succeed (this is the "
        "gap the docstring warns about). If this raises, the schema was "
        "changed to enforce deep immutability — update the docstring "
        "and this test together."
    )
    # Defensive cleanup so the mutation does not leak out of this test
    # (the snapshot is tmp_path-scoped, but we keep the assertion tidy).
    snapshot.dataset.cases.pop()
    assert len(snapshot.dataset.cases) == before_count
