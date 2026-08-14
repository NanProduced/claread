from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from claread_eval.reader_record_ask.dataset_identity import (
    DatasetIdentity,
    compute_dataset_identity_from_bytes,
)
from claread_eval.reader_record_ask.schema import (
    AtomicExpectedFact,
    ReaderRecordAskCase,
    ReaderRecordAskDataset,
)

# Subdirectories that are NOT part of the dataset content. Mirrors the
# constant in ``dataset_identity.py`` — kept private here so the loader
# does not need to import it just to exclude stray files from a glob.
# ``runs/`` holds per-run artifacts/reports; ``.cache/`` is transient.
_EXCLUDED_SUBDIRS: frozenset[str] = frozenset({"runs", ".cache"})


class ReaderRecordAskDatasetLoadError(Exception):
    """Raised when a reader-record-ask dataset cannot be loaded."""


@dataclass(frozen=True)
class LoadedReaderRecordAskDatasetSnapshot:
    """Single-byte-capture identity-bound snapshot of a loaded dataset.

    Captures ``dataset.yaml`` and all loader-resolved case files as raw
    bytes in a SINGLE read pass. Both the parsed
    parsed dataset and the :class:`DatasetIdentity`
    are derived from the SAME captured bytes, so the fingerprint CANNOT
    diverge from the parsed content — even if the files change on disk
    after capture.

    .. warning::

        This is a single-byte-capture identity-bound snapshot,
        NOT a deep immutable object. The ``@dataclass(frozen=True)``
        decorator only prevents re-binding the snapshot's top-level
        fields — ``snapshot.dataset = ...`` raises
        ``FrozenInstanceError``. It does NOT prevent callers from
        mutating the inner dataset
        Pydantic model: ``snapshot.dataset.cases.append(...)``
        succeeds silently.

        Callers MUST NOT mutate ``snapshot.dataset`` (the Pydantic
        model is internally mutable; the frozen dataclass only prevents
        re-binding the field reference, not deep mutation).

        The identity is bound to the bytes captured at load time;
        subsequent disk mutations do NOT alter ``snapshot.identity``.
        But if a caller mutates ``snapshot.dataset`` after capture,
        runtime behavior is undefined: ``snapshot.identity`` no longer
        reflects the parsed content, and downstream coverage audits /
        identity fences may produce incorrect verdicts.

    Contract:

    1. Each file (``dataset.yaml`` + every loader-resolved case file)
       is read exactly ONCE into an immutable byte snapshot.
    2. Schema parsing and content SHA-256 are computed from the SAME
       bytes.
    3. After capture, disk mutations do NOT affect the snapshot's
       internal consistency (``dataset`` and ``identity`` remain bound
       to the captured bytes).
    4. Real phases and aggregate consume ``snapshot.identity`` — they
       MUST NOT recompute the identity by re-scanning files.

    The snapshot is intentionally a deep module: a caller obtains it
    via the snapshot-loading entrypoint, then reads
    ``snapshot.dataset`` (for cases/schema) and ``snapshot.identity``
    (for artifact stamping / fence checks). The byte capture itself is
    not exposed on the public interface — callers do not need it.
    """

    dataset: ReaderRecordAskDataset
    identity: DatasetIdentity


def load_reader_record_ask_dataset_with_snapshot(
    dataset_dir: str | Path,
) -> LoadedReaderRecordAskDatasetSnapshot:
    """Load a dataset AND its identity from a single byte capture.

    This is the canonical entrypoint. ``dataset.yaml`` and every
    loader-resolved case file is read into a byte buffer exactly once;
    the parsed dataset and the
    :class:`DatasetIdentity` are both derived from those captured bytes.

    If the files change on disk AFTER this function returns, the
    snapshot's ``dataset`` and ``identity`` remain consistent — the
    fingerprint binds exactly the bytes the parser consumed.

    Raises :class:`ReaderRecordAskDatasetLoadError` on any structural
    problem (missing directory / dataset.yaml, duplicate case ids,
    invalid JSON, schema validation failure, overlapping case globs
    that resolve to the same file).
    """
    dataset_dir = Path(dataset_dir)
    if not dataset_dir.is_dir():
        raise ReaderRecordAskDatasetLoadError(
            f"evaluation run dataset directory not found: {dataset_dir}"
        )

    yaml_path = dataset_dir / "dataset.yaml"
    if not yaml_path.is_file():
        raise ReaderRecordAskDatasetLoadError(
            f"dataset.yaml not found in {dataset_dir}"
        )

    # ----- Capture 1: dataset.yaml bytes (single read) -----
    yaml_bytes = yaml_path.read_bytes()
    raw = yaml.safe_load(yaml_bytes.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ReaderRecordAskDatasetLoadError(
            f"dataset.yaml must be a mapping, got {type(raw).__name__}"
        )

    dataset = ReaderRecordAskDataset.model_validate(raw)

    # ----- Capture 2: case file bytes (single read per file) -----
    # The bytes are captured ONCE here and reused for both JSON parsing
    # and identity hash. The list of (rel_posix, bytes) tuples is the
    # input to compute_dataset_identity_from_bytes().
    case_contributions: list[tuple[str, bytes]] = []
    cases: list[ReaderRecordAskCase] = []
    seen_ids: set[str] = set()
    seen_paths: set[Path] = set()
    for glob_pattern in dataset.case_globs:
        for case_path in sorted(dataset_dir.glob(glob_pattern)):
            # Defensive: detect overlapping case_globs that resolve to
            # the same file. The loader must not parse the same file
            # twice (would produce duplicate case ids AND double-count
            # the bytes in the identity hash).
            real_path = case_path.resolve()
            if real_path in seen_paths:
                raise ReaderRecordAskDatasetLoadError(
                    f"Overlapping case_globs matched the same file twice: "
                    f"{case_path} (pattern={glob_pattern!r})"
                )
            seen_paths.add(real_path)

            rel = case_path.relative_to(dataset_dir)
            rel_posix = rel.as_posix()
            first_part = rel_posix.split("/", 1)[0]
            if first_part in _EXCLUDED_SUBDIRS:
                # Defensive: a glob should not match files under
                # runs/ or .cache/, but if it does, exclude them
                # from both parsing and identity hashing.
                continue

            file_bytes = case_path.read_bytes()
            case_raw = json.loads(file_bytes.decode("utf-8"))
            case = ReaderRecordAskCase.model_validate(case_raw)
            # Loader-owned provenance. Inspect the
            # RAW JSON dict (before any Pydantic coercion or legacy
            # migration) to determine whether this case explicitly
            # authored ``atomic_facts`` or relies on the loader's auto-
            # migration from ``required_article_facts``. The result is
            # written to the case's ``_atomic_facts_origin`` PrivateAttr
            # (not a JSON-parseable field) so dataset authors CANNOT
            # forge ``"explicit"`` provenance by setting a field in the
            # JSON file. The preflight guard reads
            # typed ``atomic_facts_origin`` property.
            raw_expected = case_raw.get("expected") or {}
            raw_atomic_facts = raw_expected.get("atomic_facts") or []
            raw_required_article_facts = (
                raw_expected.get("required_article_facts") or []
            )
            if not raw_atomic_facts and raw_required_article_facts:
                case._atomic_facts_origin = "legacy_migrated"
            else:
                case._atomic_facts_origin = "explicit"
            _migrate_legacy_required_article_facts(case)
            if case.id in seen_ids:
                raise ReaderRecordAskDatasetLoadError(
                    f"Duplicate case id: {case.id} (from {case_path})"
                )
            seen_ids.add(case.id)
            cases.append(case)
            case_contributions.append((rel_posix, file_bytes))

    dataset.cases = cases

    # ----- Derive identity from the SAME captured bytes -----
    identity = compute_dataset_identity_from_bytes(
        dataset_id=dataset.id,
        schema_version=dataset.schema_version,
        yaml_bytes=yaml_bytes,
        case_contributions=case_contributions,
    )

    return LoadedReaderRecordAskDatasetSnapshot(dataset=dataset, identity=identity)


def load_reader_record_ask_dataset(
    dataset_dir: str | Path,
) -> ReaderRecordAskDataset:
    """Load a reader-record-ask dataset from disk.

    Backwards-compat thin adapter: returns only the parsed dataset,
    discarding the identity. New callers should use
    the snapshot-loading entrypoint to obtain a snapshot whose
    ``dataset`` and ``identity`` are derived from the same byte capture
    (atomic snapshot contract).

    Expected layout::

        <dataset_dir>/dataset.yaml
        <dataset_dir>/cases/*.json (one case model per file)

    Legacy field compatibility:
    - ``required_article_facts: list[str]`` is auto-converted to
      ``atomic_facts: list[AtomicExpectedFact]`` (one alias group per
      fact, single alias = the original sentence). Both old and new
      fields may coexist; when both are present, ``atomic_facts`` wins.
    """
    return load_reader_record_ask_dataset_with_snapshot(dataset_dir).dataset


def _migrate_legacy_required_article_facts(
    case: ReaderRecordAskCase,
) -> None:
    """Auto-convert legacy ``required_article_facts`` to ``atomic_facts``.

    Spec: backwards compatibility — existing cases that still use
    ``required_article_facts`` continue to load. Each legacy fact becomes
    a single :class:`AtomicExpectedFact` with one alias group containing
    the original sentence as its single alias.

    When a case already declares ``atomic_facts``, the legacy field is
    ignored (new contract wins).

    The per-fact ``origin`` field has been REMOVED from
    :class:`AtomicExpectedFact`. Provenance is now LOADER-OWNED and
    tracked on the case via ``_atomic_facts_origin`` (set by the loader
    BEFORE this function is called, based on raw JSON inspection). The
    ``fact_id`` pattern ``legacy-{idx}`` is retained for human-
    readability but is NO LONGER the source of truth — the typed
    ``atomic_facts_origin`` property is.
    """
    if case.expected.atomic_facts:
        return  # new contract already in use
    if not case.expected.required_article_facts:
        return
    case.expected.atomic_facts = [
        AtomicExpectedFact(
            fact_id=f"legacy-{idx}",
            answer_alias_groups=[[sentence]],
            source_aliases=[],
            required=True,
            severity="high",
        )
        for idx, sentence in enumerate(case.expected.required_article_facts)
    ]


def serialize_dataset(dataset: ReaderRecordAskDataset) -> str:
    """Serialize a dataset to a JSON string (indent=2, non-ASCII preserved)."""
    return dataset.model_dump_json(indent=2, ensure_ascii=False)


def validate_round_trip(dataset: ReaderRecordAskDataset) -> bool:
    """Serialize then re-validate; True if the rebuilt dataset is field-equal."""
    serialized = serialize_dataset(dataset)
    rebuilt = ReaderRecordAskDataset.model_validate_json(serialized)
    return rebuilt.model_dump() == dataset.model_dump()
