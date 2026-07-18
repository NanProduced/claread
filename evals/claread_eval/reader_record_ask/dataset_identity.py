"""Deterministic content identity for the R4-A3 working dataset.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirement (P0-2 dataset identity): the R4-A3 working dataset lives
under ``evals/tmp/`` and is gitignored, so the same ``dataset_id`` can
silently drift between phases. This module computes a deterministic
SHA-256 fingerprint over the dataset's actual file content (``dataset.yaml``
+ all loader-resolved case files) so artifacts can carry an auditable
content identity, and Phase 2/3/aggregate can fail-closed on drift.

P1-b atomic snapshot (this rework): the canonical entrypoint is now
:func:`compute_dataset_identity_from_bytes`, which operates on bytes
captured by the loader in a single read pass. The legacy
:func:`compute_dataset_identity` is kept as a backwards-compatible
adapter that re-reads files from disk — it produces the same hash for
the same bytes but does NOT bind the identity to the bytes the parser
actually consumed. New callers should use
:func:`load_r4_a3_dataset_with_snapshot` from ``loader.py`` to get a
snapshot where the parsed dataset and identity are derived from the
same byte capture.

Design constraints (spec §二):

1. Uses SHA-256, lowercase 64-char hex.
2. Inputs include at minimum:
   - ``dataset.yaml`` raw bytes
   - every ``cases/*.json`` (or other ``case_globs`` patterns) the
     loader actually parses
3. Files are sorted by POSIX-style relative path (forward slashes).
4. The hash binds BOTH relative path AND raw file bytes — renaming a
   case file changes the fingerprint, and so does editing its content.
5. Excludes: absolute local path, mtime/ctime, the ``evals/tmp/`` prefix,
   the ``runs/`` subdirectory, and any artifact/report output.
6. Framing is unambiguous: each contribution is
   ``u64_be(path_len) || path_bytes || u64_be(content_len) || content_bytes``.
   Simple string concatenation is forbidden (path/content boundary
   could shift).
7. The same dataset copied to a different absolute directory produces
   the same fingerprint (only relative paths + bytes are hashed).
8. Any change to a case file's content, filename, or to ``dataset.yaml``
   changes the fingerprint.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Dataset,
)

# Subdirectories that are NOT part of the dataset content identity.
# ``runs/`` holds per-run artifacts/reports; ``.cache/`` would hold
# transient caches. These are excluded even if a glob matched them.
_EXCLUDED_SUBDIRS: frozenset[str] = frozenset({"runs", ".cache"})


@dataclass(frozen=True)
class DatasetIdentity:
    """Content identity for an R4-A3 dataset at a point in time.

    - ``dataset_id``: the dataset's declared id (e.g. ``reader-record-ask-r4-a3``).
    - ``schema_version``: the dataset's declared schema version.
    - ``content_sha256``: SHA-256 over ``dataset.yaml`` + all loader-resolved
      case files (length-prefixed framing, POSIX relative paths, sorted).

    Two datasets with the same :class:`DatasetIdentity` are byte-for-byte
    equivalent (modulo absolute path). A change in any case file's
    content, filename, or in ``dataset.yaml`` produces a different
    ``content_sha256``.
    """

    dataset_id: str
    schema_version: str
    content_sha256: str


class DatasetIdentityError(RuntimeError):
    """Raised when dataset identity cannot be computed or verified.

    The message is intentionally generic (no absolute paths, no file
    content). Carries only:
    - which check failed (``compute`` / ``mismatch`` / ``missing_field``)
    - the dataset_id (safe to surface)
    - a short reason code
    """

    def __init__(self, *, reason: str, dataset_id: str | None = None) -> None:
        self.reason = reason
        self.dataset_id = dataset_id
        msg = f"dataset_identity_error: reason={reason}"
        if dataset_id is not None:
            msg += f" dataset_id={dataset_id}"
        super().__init__(msg)


def compute_dataset_identity_from_bytes(
    *,
    dataset_id: str,
    schema_version: str,
    yaml_bytes: bytes,
    case_contributions: list[tuple[str, bytes]],
) -> DatasetIdentity:
    """Compute :class:`DatasetIdentity` from already-captured bytes.

    This is the canonical entrypoint for the P1-b atomic snapshot
    contract. The loader captures ``dataset.yaml`` and every
    loader-resolved case file as raw bytes in a single read pass, then
    calls this function with the captured bytes. The returned
    :class:`DatasetIdentity` is therefore bound to exactly the bytes
    the parser consumed — even if the files change on disk after
    capture, the identity remains stable.

    Inputs:
    - ``yaml_bytes``: raw bytes of ``dataset.yaml``.
    - ``case_contributions``: list of ``(rel_posix, file_bytes)`` for
      each loader-resolved case file. The relative path uses POSIX
      forward slashes and is relative to the dataset directory.

    The function does NOT read from disk. Sorting is performed
    internally (by POSIX relative path) so callers may pass
    contributions in any order.

    Excluded subdirectories (``runs/``, ``.cache/``) are NOT filtered
    here — the caller (loader) is responsible for excluding them when
    building ``case_contributions``. This keeps the boundary clean:
    the loader decides which files are part of the dataset content;
    this function only hashes what it is given.
    """
    hasher = hashlib.sha256()

    # 1. dataset.yaml — relative path is the fixed literal "dataset.yaml".
    _framed_update(hasher, b"dataset.yaml", yaml_bytes)

    # 2. All loader-resolved case files, sorted by POSIX relative path
    #    for stable, order-independent hashing. The caller may pass
    #    contributions in glob/sorted order, but we re-sort here so the
    #    hash is independent of the input order.
    sorted_contribs = sorted(case_contributions, key=lambda item: item[0])
    for rel_posix, file_bytes in sorted_contribs:
        _framed_update(hasher, rel_posix.encode("utf-8"), file_bytes)

    return DatasetIdentity(
        dataset_id=dataset_id,
        schema_version=schema_version,
        content_sha256=hasher.hexdigest(),
    )


def compute_dataset_identity(
    dataset_dir: Path,
    dataset: ReaderRecordAskR4A3Dataset,
) -> DatasetIdentity:
    """Compute :class:`DatasetIdentity` for a loaded dataset.

    Legacy entrypoint: re-reads ``dataset_dir/dataset.yaml`` and every
    file matched by ``dataset.case_globs`` from disk, then delegates to
    :func:`compute_dataset_identity_from_bytes`.

    DEPRECATED for new code: this function re-reads files from disk
    AFTER the loader has already parsed them, so the fingerprint is
    NOT bound to the bytes the parser actually consumed. If the files
    change between the loader's read and this function's read, the
    parsed dataset and the computed identity can diverge silently.

    New callers should use
    :func:`load_r4_a3_dataset_with_snapshot` from ``loader.py`` to
    obtain a :class:`LoadedReaderRecordAskDatasetSnapshot` whose
    ``dataset`` and ``identity`` fields are derived from the SAME byte
    capture.
    """
    dataset_dir = Path(dataset_dir).resolve()
    yaml_path = dataset_dir / "dataset.yaml"
    if not yaml_path.is_file():
        raise DatasetIdentityError(
            reason="dataset_yaml_missing",
            dataset_id=dataset.id,
        )

    yaml_bytes = yaml_path.read_bytes()

    contributions: list[tuple[str, bytes]] = []
    for pattern in dataset.case_globs:
        for case_path in sorted(dataset_dir.glob(pattern)):
            rel = case_path.relative_to(dataset_dir)
            rel_posix = rel.as_posix()
            # Exclude run/cache subdirectories defensively — even if a
            # glob pattern somehow matched a file under ``runs/``, we
            # must not include it in the content identity.
            first_part = rel_posix.split("/", 1)[0]
            if first_part in _EXCLUDED_SUBDIRS:
                continue
            contributions.append((rel_posix, case_path.read_bytes()))

    return compute_dataset_identity_from_bytes(
        dataset_id=dataset.id,
        schema_version=dataset.schema_version,
        yaml_bytes=yaml_bytes,
        case_contributions=contributions,
    )


def _framed_update(
    hasher: hashlib._Hash,
    path_bytes: bytes,
    content_bytes: bytes,
) -> None:
    """Update ``hasher`` with a length-prefixed ``(path, content)`` frame.

    Frame layout (binary, big-endian)::

        u64_be(len(path_bytes))   || path_bytes   ||
        u64_be(len(content_bytes)) || content_bytes

    Length-prefix framing makes the boundary between path and content
    unambiguous — simple string concatenation would allow a path ending
    in ``5`` followed by content ``abc`` to collide with a path ``5abc``
    followed by empty content.
    """
    path_len = len(path_bytes).to_bytes(8, "big")
    content_len = len(content_bytes).to_bytes(8, "big")
    hasher.update(path_len)
    hasher.update(path_bytes)
    hasher.update(content_len)
    hasher.update(content_bytes)


# ---------------------------------------------------------------------------
# Verification helpers (used by Phase 2/3 preflight and aggregate)
# ---------------------------------------------------------------------------


def assert_prior_artifacts_identity_consistent(
    prior_artifacts: list,
    *,
    current_identity: DatasetIdentity,
) -> None:
    """Fail-closed when prior artifacts carry mixed or mismatched identity.

    Checks (spec §二.4):
    1. Every prior artifact must carry non-empty ``dataset_id``,
       ``dataset_schema_version``, and ``dataset_content_sha256``.
       Missing fields → :class:`DatasetIdentityError` (reason=
       ``prior_missing_identity_field``). Old local artifacts without
       fingerprints are treated as unauditable and fail-closed.
    2. All prior artifacts must carry the SAME identity (no mixed
       fingerprints). Mixed → ``prior_mixed_identity``.
    3. The prior identity must match ``current_identity`` exactly
       (dataset_id, schema_version, content_sha256). Mismatch →
       ``prior_current_mismatch``.

    Raises :class:`DatasetIdentityError` on any violation. The caller
    (harness preflight) MUST translate this into a fail-closed skip
    BEFORE any paid provider call.
    """
    if not prior_artifacts:
        return  # nothing to verify

    seen_identities: set[tuple[str, str, str]] = set()
    for artifact in prior_artifacts:
        ds_id = getattr(artifact, "dataset_id", None)
        schema = getattr(artifact, "dataset_schema_version", None)
        sha = getattr(artifact, "dataset_content_sha256", None)
        if not ds_id or not schema or not sha:
            raise DatasetIdentityError(
                reason="prior_missing_identity_field",
                dataset_id=current_identity.dataset_id,
            )
        seen_identities.add((ds_id, schema, sha))

    if len(seen_identities) != 1:
        raise DatasetIdentityError(
            reason="prior_mixed_identity",
            dataset_id=current_identity.dataset_id,
        )

    prior_tuple = next(iter(seen_identities))
    current_tuple = (
        current_identity.dataset_id,
        current_identity.schema_version,
        current_identity.content_sha256,
    )
    if prior_tuple != current_tuple:
        raise DatasetIdentityError(
            reason="prior_current_mismatch",
            dataset_id=current_identity.dataset_id,
        )


def find_identity_mismatched_artifacts(
    artifacts: list,
    *,
    current_identity: DatasetIdentity,
) -> list:
    """Return artifacts whose identity does NOT match ``current_identity``.

    Used by aggregate (spec §二.5): artifacts with missing or mismatched
    identity are NOT silently skipped and NOT treated as pass/fail —
    they are returned so the caller can refuse to produce a normal
    verdict. The caller MUST NOT include them in case_results.
    """
    mismatched: list = []
    for artifact in artifacts:
        ds_id = getattr(artifact, "dataset_id", None)
        schema = getattr(artifact, "dataset_schema_version", None)
        sha = getattr(artifact, "dataset_content_sha256", None)
        if not ds_id or not schema or not sha:
            mismatched.append(artifact)
            continue
        if (ds_id, schema, sha) != (
            current_identity.dataset_id,
            current_identity.schema_version,
            current_identity.content_sha256,
        ):
            mismatched.append(artifact)
    return mismatched
