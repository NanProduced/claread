"""ReaderRecordAskRunManifest — run-level completion state persistence.

Spec: `.trae/specs/audit-r4-a3-eval-harness-final-closure/spec.md`
Requirement: Run Manifest Persistence.

Prior to this module, budget/incomplete run state was lost across pytest
subprocess boundaries: ``_execute_phase()`` caught ``BudgetExhaustedError``
and returned an in-memory ``BudgetStopResult``; once the phase process
exited, the budget stop state was gone. The aggregate could only infer
budget status from on-disk ``RawArtifact.budget_exhausted`` flags, but
the real phase never wrote such artifacts. Result: partial artifacts on
disk + lost budget state → aggregate could incorrectly accept a partial
run.

This module provides a deep, serializable manifest that persists the
run's completion state (status / planned / completed / remaining /
identity / usage counters / stop_reason) to a single JSON file resolved
by :meth:`RunSessionLayout.manifest_path`. Both the harness (writer) and
the aggregate (reader) MUST use that resolver — they MUST NOT hand-build
the path.

The manifest is fail-closed: corrupt JSON, missing fields, wrong types,
or an invariant violation (planned != completed + remaining per case)
all raise :class:`RunManifestError` (reason=``corrupt_manifest``).
Serialized manifests never contain prompts, answers, reasoning, article
text, API keys, or exception text — ``stop_reason`` is restricted to an
allowlist (``None`` for completed, ``"budget_exhausted"`` for budget stop).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

RunManifestStatus = Literal["completed", "budget_exhausted"]

#: Fixed manifest schema version. Writers always emit this value; readers
#: reject manifests carrying a different version (forward / backward
#: incompatibility is fail-closed).
MANIFEST_SCHEMA_VERSION: str = "reader_record_ask.run_manifest/v1"

#: R4-A4-2R3 audit contract version. Distinguishes manifests that
#: enforce the strict V2 identity/budget contract from legacy V1
#: (R4-A4-2R2) manifests. V2 requires:
#:
#: - ``runtime_fixture_identities`` covers every key in
#:   ``planned_run_indices`` exactly (no missing, no extra).
#: - Each identity value is a strict 64-char lowercase hex SHA-256.
#: - ``planned_logical_runs == manifest.planned_count``.
#: - ``retry_policy`` is a typed dict with ``tool_max_retries`` /
#:   ``output_max_retries`` non-negative ints.
#: - ``retry_headroom`` is a non-negative int (NOT null).
#: - ``request_cap`` is a non-negative int (NOT null).
#:
#: V1 (legacy) compat is selected by EXPLICIT version (``"r4-a4-2r2"``
#: or ``None``), NOT by guessing from an empty ``runtime_fixture_identities``
#: dict. R4-A4-2R3 closes the "empty dict bypass" — a V2 manifest with
#: an empty identity map is corrupt, not legacy.
AUDIT_CONTRACT_VERSION_V1: str = "r4-a4-2r2"
AUDIT_CONTRACT_VERSION_V2: str = "r4-a4-2r3"
_AUDIT_CONTRACT_VERSIONS: frozenset[str | None] = frozenset(
    {AUDIT_CONTRACT_VERSION_V1, AUDIT_CONTRACT_VERSION_V2, None}
)

#: Required keys for V2 ``retry_policy`` dict.
_RETRY_POLICY_REQUIRED_KEYS: tuple[str, ...] = (
    "tool_max_retries",
    "output_max_retries",
)

#: Allowlist of safe stop_reason values. ``None`` is emitted for
#: ``status="completed"``; ``"budget_exhausted"`` for budget stops.
#: Exception text, provider diagnostics, URLs, and API keys are never
#: written into ``stop_reason``.
_ALLOWED_STOP_REASONS: frozenset[str | None] = frozenset({None, "budget_exhausted"})

#: Allowlist of valid phase numbers (1/2/3). bool is rejected separately
#: because ``isinstance(True, int)`` is True in Python.
_ALLOWED_PHASES: frozenset[int] = frozenset({1, 2, 3})

#: Strict SHA-256 lowercase hex pattern (exactly 64 lowercase hex chars).
#: Used to validate ``dataset_content_sha256``. Uppercase hex, shorter
#: or longer strings, and non-hex characters are rejected.
_SHA256_LOWERCASE_HEX_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")

#: Required top-level fields in the serialized manifest. Used by
#: :meth:`ReaderRecordAskRunManifest.from_json` to fail-closed on
#: missing-field corruption.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version",
    "run_id",
    "phase",
    "dataset_id",
    "dataset_schema_version",
    "dataset_content_sha256",
    "status",
    "planned_run_indices",
    "completed_run_indices",
    "remaining_run_indices",
    "executed_requests",
    "executed_tokens",
    "stop_reason",
)


class RunManifestError(Exception):
    """Raised when a manifest cannot be read or fails validation.

    Carries only:
    - ``reason``: a short allowlisted code (currently always
      ``"corrupt_manifest"``). Safe to surface.
    - ``path``: the manifest file path (str) when the error was raised
      from :func:`read_manifest`; ``None`` when raised from
      :meth:`ReaderRecordAskRunManifest.from_json` without a path.

    The message never contains file content or exception text.
    """

    def __init__(self, *, reason: str, path: str | None = None) -> None:
        self.reason = reason
        self.path = path
        msg = f"run_manifest_error: reason={reason}"
        if path is not None:
            msg += f" path={path}"
        super().__init__(msg)


class ManifestState(str, Enum):
    """Three-state classification of a manifest file on disk.

    - ``ABSENT``: the manifest file does not exist (the run has not yet
      written one).
    - ``VALID``: the file exists and parses + passes the full strict
      contract validation (schema, semantic, and coverage invariants).
    - ``CORRUPT``: the file exists but is unparseable, missing fields,
      has wrong types, or violates the strict contract. The run is
      unauditable.

    The aggregate uses this state (plus ``manifest_run_id_matches``)
    to decide the verdict:

    - absent + 0 artifacts  → blocked_by_real_model_run
    - absent + >0 artifacts → blocked_incomplete_real_model_run
    - corrupt + any         → blocked_incomplete_real_model_run
    - valid + run_id mismatch (foreign) → blocked_incomplete_real_model_run
    - valid + run_id match  → normal coverage-audit path

    The corrupt state MUST NOT be folded into absent — a corrupt
    manifest indicates the run started but its audit trail is broken,
    which is a strictly worse state than "never ran".
    """

    ABSENT = "absent"
    VALID = "valid"
    CORRUPT = "corrupt"


@dataclass(frozen=True)
class ManifestReadResult:
    """Typed result of :func:`read_manifest_with_state`.

    Carries the three-state classification plus the parsed manifest
    (only when ``state == ManifestState.VALID``). Error details are
    deliberately NOT carried — the report must not surface file
    content, exception text, or local sensitive information.
    """

    state: ManifestState
    manifest: ReaderRecordAskRunManifest | None = None


@dataclass(frozen=True)
class ReaderRecordAskRunManifest:
    """Persistent run-level completion state for one R4-A3 phase run.

    A single source of truth for whether a run completed, what it
    planned to do, what it actually did, and what remains. The manifest
    is written atomically by the harness and read by the aggregate to
    decide whether the run's artifacts are eligible for evaluation.

    Field semantics:

    - ``schema_version``: fixed manifest schema version
      (``reader_record_ask.run_manifest/v1``). Used to reject manifests
      produced by a different (incompatible) writer.
    - ``run_id``: the run id from :class:`RunSessionLayout`.
    - ``phase``: phase number (1/2/3).
    - ``dataset_id`` / ``dataset_schema_version`` / ``dataset_content_sha256``:
      the dataset identity triple bound to this run (from
      ``load_r4_a3_dataset_with_snapshot()``). Artifacts MUST carry the
      same triple; mismatch indicates dataset drift mid-run.
    - ``status``: ``completed`` (all planned repetitions done) or
      ``budget_exhausted`` (stopped early due to budget).
    - ``planned_run_indices``: ``{case_id: [run_index, ...]}`` for every
      repetition the harness intended to execute.
    - ``completed_run_indices``: ``{case_id: [run_index, ...]}`` for
      repetitions whose artifacts were actually written to disk.
    - ``remaining_run_indices``: ``{case_id: [run_index, ...]}`` for
      repetitions that did NOT complete. For ``status="completed"``
      this is empty. For ``status="budget_exhausted"`` it includes the
      in-flight case's unfinished indices and all subsequent cases'
      full planned indices.
    - ``executed_requests`` / ``executed_tokens``: total provider
      requests and tokens consumed by this run (across all artifacts).
      Used for budget telemetry.
    - ``stop_reason``: ``None`` for completed runs; ``"budget_exhausted"``
      for budget-stopped runs. Allowlisted — never contains exception
      text or provider diagnostics.

    Invariant (checked in :meth:`from_json`): for every ``case_id``,
    ``planned == completed ∪ remaining`` (as sets) and
    ``completed ∩ remaining == ∅``. Violations indicate a corrupt or
    hand-edited manifest and fail-closed with ``RunManifestError``.
    """

    schema_version: str
    run_id: str
    phase: int
    dataset_id: str
    dataset_schema_version: str
    dataset_content_sha256: str
    status: RunManifestStatus
    planned_run_indices: dict[str, list[int]]
    completed_run_indices: dict[str, list[int]]
    remaining_run_indices: dict[str, list[int]]
    executed_requests: int
    executed_tokens: int
    stop_reason: str | None
    # R4-A4-2R2 P0-2: per-case runtime fixture identity map. The
    # harness writes the case's ``expected_runtime_fixture_fingerprint``
    # (== the preflight-computed actual fingerprint) for every case in
    # ``planned_run_indices``. The aggregate compares this against
    # each artifact's ``runtime_fixture_fingerprint`` (three-layer
    # check: dataset expected == manifest identity == artifact actual).
    # Empty for backwards compat with pre-R4-A4-2R2 manifests.
    runtime_fixture_identities: dict[str, str] = field(default_factory=dict)
    # R4-A4-2R2 P1: self-contained budget audit fields. The harness
    # writes these from env/config at run time; the aggregate reads
    # them from the manifest (NOT from current shell env) so a
    # historical run's budget is auditable without reconstructing the
    # env. Defaults preserve backwards compat with pre-R4-A4-2R2
    # manifests (the aggregate surfaces ``None`` / 0 for old manifests
    # and falls back to env-derived values for ``request_cap`` only
    # when the manifest field is absent).
    planned_logical_runs: int = 0
    request_cap: int | None = None
    token_cap: int | None = None
    # R4-A4-2R3 P1: ``retry_policy`` is now a typed dict
    # ``{"tool_max_retries": int, "output_max_retries": int}`` (V2) or
    # an empty dict (V1 legacy). The V2 contract is enforced in
    # :func:`_parse_and_validate_manifest_dict`.
    retry_policy: dict[str, Any] = field(default_factory=dict)
    retry_headroom: int | None = None
    # R4-A4-2R3 P0-2: explicit audit contract version. ``None`` or
    # ``"r4-a4-2r2"`` selects V1 (legacy) compat — current R4-A4-2R2
    # rules apply. ``"r4-a4-2r3"`` selects V2 strict — the manifest
    # MUST satisfy the strict identity/budget contract documented on
    # :data:`AUDIT_CONTRACT_VERSION_V2`. The aggregate uses this field
    # (NOT empty-dict guessing) to decide which validation path to
    # apply.
    audit_contract_version: str | None = None

    # ------------------------------------------------------------------
    # Counts (read-only projections over the index dicts)
    # ------------------------------------------------------------------

    @property
    def planned_count(self) -> int:
        """Total number of planned repetitions across all cases."""
        return sum(len(v) for v in self.planned_run_indices.values())

    @property
    def completed_count(self) -> int:
        """Total number of completed repetitions across all cases."""
        return sum(len(v) for v in self.completed_run_indices.values())

    @property
    def remaining_count(self) -> int:
        """Total number of remaining repetitions across all cases."""
        return sum(len(v) for v in self.remaining_run_indices.values())

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def identity_tuple(self) -> tuple[str, str, str]:
        """Return ``(dataset_id, dataset_schema_version, dataset_content_sha256)``.

        Used by :func:`validate_manifest_coverage` to compare against
        each artifact's identity triple.
        """
        return (
            self.dataset_id,
            self.dataset_schema_version,
            self.dataset_content_sha256,
        )

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return ``True`` iff this manifest represents a fully completed run.

        True only when ALL hold:
        - ``status == "completed"``
        - ``remaining_count == 0``
        - ``planned_count == completed_count``
        - per-case sorted-list equality (NOT set equality — duplicates
          must be detected, not silently deduplicated).
        - no duplicate run indices within any planned/completed list
          (defense-in-depth: ``from_json`` already rejects duplicates,
          but ``is_complete`` checks again so an in-memory dataclass
          constructed without going through ``from_json`` is still
          caught).
        - no extra cases in ``completed_run_indices`` beyond what is
          in ``planned_run_indices``.

        The previous implementation used ``set(planned) == set(completed)``
        which silently deduplicated ``[0, 0]`` → ``{0}``, allowing a
        manifest with duplicate indices to masquerade as complete. This
        is the root cause of the minimal reproduction bug
        (planned=[0,0], completed=[0,0] → is_complete=True). The fix
        uses sorted-list comparison and explicit duplicate detection.
        """
        if self.status != "completed":
            return False
        if self.remaining_count != 0:
            return False
        if self.planned_count != self.completed_count:
            return False
        # Defense-in-depth: reject duplicates within any list. An
        # in-memory dataclass constructed without from_json might
        # carry duplicates; from_json rejects them, but is_complete
        # checks again so the contract holds regardless of construction
        # path.
        for _case_id, planned in self.planned_run_indices.items():
            if len(set(planned)) != len(planned):
                return False
        for _case_id, completed in self.completed_run_indices.items():
            if len(set(completed)) != len(completed):
                return False
        # Per-case sorted-list equality (NOT set equality).
        for case_id, planned in self.planned_run_indices.items():
            completed = self.completed_run_indices.get(case_id, [])
            if sorted(planned) != sorted(completed):
                return False
        # No extra cases in completed beyond planned.
        for case_id in self.completed_run_indices:
            if case_id not in self.planned_run_indices:
                return False
        return True

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialize to a stable JSON string.

        ``indent=2``, ``sort_keys=True``, ``ensure_ascii=False`` — stable
        across processes so byte-level comparison is meaningful.
        """
        return json.dumps(
            dataclasses.asdict(self),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, s: str) -> ReaderRecordAskRunManifest:
        """Deserialize from JSON, fail-closed on corruption.

        Raises :class:`RunManifestError` (reason=``corrupt_manifest``)
        if the JSON is unparseable, the top-level value is not an
        object, any required field is missing, any field has the wrong
        type, ``schema_version`` does not match
        :data:`MANIFEST_SCHEMA_VERSION`, ``stop_reason`` is not in
        :data:`_ALLOWED_STOP_REASONS`, or the per-case invariant
        (``planned == completed ∪ remaining``, disjoint) is violated.

        R4-A4-2R2: the six new fields (``runtime_fixture_identities``,
        ``planned_logical_runs``, ``request_cap``, ``token_cap``,
        ``retry_policy``, ``retry_headroom``) are OPTIONAL for
        backwards compatibility with pre-R4-A4-2R2 manifests. When
        absent they fall back to the dataclass defaults. When present
        they MUST pass strict type validation in
        :func:`_parse_and_validate_manifest_dict`.

        R4-A4-2R3: ``audit_contract_version`` is OPTIONAL (defaults to
        ``None`` = V1 legacy). When present and equal to
        :data:`AUDIT_CONTRACT_VERSION_V2`, the strict V2 contract is
        enforced (identity map covers planned, keys match, planned
        runs equal, retry_policy typed dict, retry_headroom non-null,
        request_cap non-null).
        """
        data = _parse_and_validate_manifest_dict(s)
        return cls(
            schema_version=data["schema_version"],
            run_id=data["run_id"],
            phase=data["phase"],
            dataset_id=data["dataset_id"],
            dataset_schema_version=data["dataset_schema_version"],
            dataset_content_sha256=data["dataset_content_sha256"],
            status=data["status"],
            planned_run_indices=data["planned_run_indices"],
            completed_run_indices=data["completed_run_indices"],
            remaining_run_indices=data["remaining_run_indices"],
            executed_requests=data["executed_requests"],
            executed_tokens=data["executed_tokens"],
            stop_reason=data["stop_reason"],
            # R4-A4-2R2 optional fields — use .get() with defaults so
            # pre-R4-A4-2R2 manifests (which never carried these keys)
            # round-trip cleanly.
            runtime_fixture_identities=data.get(
                "runtime_fixture_identities", {}
            ),
            planned_logical_runs=data.get("planned_logical_runs", 0),
            request_cap=data.get("request_cap", None),
            token_cap=data.get("token_cap", None),
            # R4-A4-2R3: retry_policy is now a dict (V2 typed contract)
            # or empty dict (V1 legacy). The string "default" from
            # pre-R4-A4-2R3 manifests is migrated to ``{}`` so the
            # dataclass type stays ``dict[str, Any]``.
            retry_policy=data.get("retry_policy", {}),
            retry_headroom=data.get("retry_headroom", None),
            # R4-A4-2R3: audit_contract_version (None / "r4-a4-2r2" /
            # "r4-a4-2r3").
            audit_contract_version=data.get("audit_contract_version", None),
        )


# ---------------------------------------------------------------------------
# Atomic write / read
# ---------------------------------------------------------------------------


def write_manifest_atomic(
    manifest: ReaderRecordAskRunManifest,
    manifest_path: Path,
) -> None:
    """Atomically write ``manifest`` to ``manifest_path``.

    Implements:
    - Parent directory is created (``parents=True, exist_ok=True``).
    - **Pre-write validation**: ``ReaderRecordAskRunManifest.from_json(
      manifest.to_json())`` is called BEFORE writing to disk. This
      rejects hand-constructed dataclass instances that violate the
      strict contract (duplicate indices, status/stop_reason
      inconsistency, etc.). An invalid dataclass MUST NOT successfully
      land on disk.
    - JSON is written to a temporary file in the same directory
      (``tempfile.NamedTemporaryFile(delete=False)``).
    - ``f.flush()`` + ``os.fsync(f.fileno())`` before close.
    - ``os.replace(tmp, manifest_path)`` atomically swaps the file in.
    - **Post-write readback**: ``read_manifest(manifest_path)`` is
      called (which invokes ``from_json``) — NOT just ``json.loads``.
      This catches mid-write corruption and re-validates the full
      schema/semantic contract on the bytes that actually landed.

    On any exception the temporary file is removed if it still exists.

    Raises :class:`RunManifestError` (reason=``corrupt_manifest``) if
    the pre-write validation or post-write readback fails. This means
    a hand-constructed invalid frozen dataclass cannot be persisted.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-write validation: round-trip through from_json to enforce the
    # full strict contract. This catches dataclass instances built
    # directly (without going through from_json) that carry duplicate
    # indices, status/stop_reason inconsistency, etc. An invalid
    # dataclass MUST NOT successfully land on disk.
    json_str = manifest.to_json()
    ReaderRecordAskRunManifest.from_json(json_str)

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=manifest_path.parent,
            delete=False,
            mode="w",
            encoding="utf-8",
            suffix=".tmp",
            prefix="manifest-",
        ) as f:
            f.write(json_str)
            f.flush()
            os.fsync(f.fileno())
            tmp_path = Path(f.name)

        os.replace(tmp_path, manifest_path)
        tmp_path = None  # successfully replaced; nothing to clean up

        # Post-write readback: full from_json validation (NOT just
        # json.loads). This catches mid-write corruption and re-runs
        # the strict contract check on the bytes that actually landed.
        read_manifest(manifest_path)
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def read_manifest(manifest_path: Path) -> ReaderRecordAskRunManifest | None:
    """Read a manifest from ``manifest_path``.

    - File does not exist → return ``None``.
    - File exists but JSON unparseable, schema invalid, or invariant
      violated → raise :class:`RunManifestError` (reason=
      ``corrupt_manifest``, ``path=str(manifest_path)``).
    - File exists but bytes are not valid UTF-8 (``UnicodeDecodeError``
      / ``UnicodeError``) → raise :class:`RunManifestError` (reason=
      ``corrupt_manifest``, ``path=str(manifest_path)``). P1 fix:
      previously ``read_text(encoding="utf-8")`` propagated the
      ``UnicodeDecodeError`` out of this function, crashing the caller.
      Now it is folded into the same corrupt-manifest path so
      :func:`read_manifest_with_state` can classify it as ``CORRUPT``.
    - Otherwise → return :class:`ReaderRecordAskRunManifest`.

    UTF-8 BOM policy: strict UTF-8 is enforced (``encoding="utf-8"``).
    A leading BOM (``\\xef\\xbb\\xbf``) is NOT stripped —
    ``json.loads`` would then fail on the BOM char and the file is
    classified as corrupt. This is the fail-closed choice: operators
    who write manifests must write plain UTF-8 without BOM.
    """
    if not manifest_path.exists():
        return None
    try:
        content = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, UnicodeError) as exc:
        raise RunManifestError(
            reason="corrupt_manifest",
            path=str(manifest_path),
        ) from exc
    try:
        return ReaderRecordAskRunManifest.from_json(content)
    except RunManifestError as exc:
        # Re-raise with the file path so callers can surface where the
        # corrupt manifest lives. ``from_json`` itself has no path.
        if exc.path is None:
            raise RunManifestError(
                reason=exc.reason,
                path=str(manifest_path),
            ) from exc
        raise


def read_manifest_with_state(
    manifest_path: Path,
) -> ManifestReadResult:
    """Read a manifest from ``manifest_path`` and classify its state.

    Returns a :class:`ManifestReadResult` carrying the three-state
    classification (absent / valid / corrupt) plus the parsed manifest
    (only when valid). This is the P1 fix: the previous code caught
    ``RunManifestError`` and folded corrupt → absent, which caused
    "corrupt manifest + no artifacts" to be misclassified as
    ``blocked_by_real_model_run`` (the "never ran" verdict) instead of
    ``blocked_incomplete_real_model_run`` (the "ran but unauditable"
    verdict).

    - File does not exist → ``ManifestReadResult(state=ABSENT)``.
    - File exists but bytes are not valid UTF-8
      (``UnicodeDecodeError`` / ``UnicodeError``), OR JSON unparseable,
      OR schema invalid, OR invariant violated →
      ``ManifestReadResult(state=CORRUPT)``. P1 fix: previously
      ``UnicodeDecodeError`` propagated out of this function and crashed
      the aggregate. Now it is classified as ``CORRUPT`` so the verdict
      falls to ``blocked_incomplete_real_model_run``. Error details are
      NOT carried — the report must not surface file content or
      exception text.
    - Otherwise → ``ManifestReadResult(state=VALID, manifest=...)``.

    UTF-8 BOM policy: strict UTF-8 is enforced. A leading BOM is NOT
    stripped — ``json.loads`` fails on the BOM char and the file is
    classified as ``CORRUPT``. This is the fail-closed choice.

    The caller (aggregate) is responsible for verifying
    ``manifest.run_id == session.run_id`` after a VALID read, BEFORE
    consulting the manifest for coverage audit. A foreign manifest
    (valid content but wrong run_id) is treated as a coverage failure
    → ``blocked_incomplete_real_model_run``.
    """
    if not manifest_path.exists():
        return ManifestReadResult(state=ManifestState.ABSENT)
    try:
        content = manifest_path.read_text(encoding="utf-8")
        manifest = ReaderRecordAskRunManifest.from_json(content)
    except (RunManifestError, OSError, UnicodeDecodeError, UnicodeError):
        # Deliberately do NOT carry the exception or content — the
        # report must not surface file content or exception text.
        # ``UnicodeDecodeError`` / ``UnicodeError`` are P1 additions:
        # invalid UTF-8 bytes, truncated multi-byte sequences, and
        # binary content are now classified as ``CORRUPT`` instead of
        # crashing the aggregate.
        return ManifestReadResult(state=ManifestState.CORRUPT)
    return ManifestReadResult(
        state=ManifestState.VALID,
        manifest=manifest,
    )


# ---------------------------------------------------------------------------
# Coverage audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageAuditResult:
    """Typed result of :func:`validate_manifest_coverage`.

    All counts are non-negative integers. ``*_run_indices`` dicts map
    ``case_id`` to a sorted list of offending run indices.

    - ``manifest_present``: ``False`` when no manifest file existed.
    - ``manifest_status``: ``"completed"`` / ``"budget_exhausted"`` /
      ``None`` (no manifest).
    - ``manifest_state``: three-state classification
      (``"absent"`` / ``"valid"`` / ``"corrupt"``). P1 fix: corrupt
      MUST NOT be folded into absent — a corrupt manifest indicates
      the run started but its audit trail is broken.
    - ``manifest_run_id_matches``: ``True`` only when the manifest is
      valid AND ``manifest.run_id == session.run_id``. ``False`` when
      the manifest is foreign (wrong run_id). ``None`` when there is
      no valid manifest to compare (absent / corrupt). P0-2 fix: a
      foreign manifest MUST NOT be stitched together with the current
      run's artifacts.
    - ``planned_count`` / ``completed_count``: from the manifest
      (0 when no manifest).
    - ``missing_count``: planned (case_id, run_index) pairs with no
      artifact. 0 when no manifest (nothing is "planned").
    - ``duplicate_count``: extra artifacts beyond the first for each
      (case_id, run_index). Counted even when no manifest — duplicates
      are an artifact-side problem.
    - ``unexpected_count``: artifacts whose (case_id, run_index) is
      not in planned. 0 when no manifest (no planned set).
    - ``identity_mismatch_count``: artifacts whose dataset identity
      triple differs from the manifest's. 0 when no manifest.
    - ``evaluable_artifact_count``:
      ``total_artifacts - duplicate - unexpected - identity_mismatch``.
      Clamped to ``>= 0``. The caller MUST still consult
      ``manifest_present`` / ``manifest_status`` / ``missing_count``
      before treating artifacts as eligible for evaluation.
    - ``dataset_identity``: manifest's identity triple, or ``None``.
    - ``missing_run_indices`` / ``duplicate_run_indices`` /
      ``unexpected_run_indices``: per-case sorted run index lists.
    """

    manifest_present: bool
    manifest_status: str | None
    planned_count: int
    completed_count: int
    missing_count: int
    duplicate_count: int
    unexpected_count: int
    identity_mismatch_count: int
    evaluable_artifact_count: int
    dataset_identity: tuple[str, str, str] | None
    missing_run_indices: dict[str, list[int]]
    duplicate_run_indices: dict[str, list[int]]
    unexpected_run_indices: dict[str, list[int]]
    # P1 / P0-2 new fields. Defaults preserve backward compatibility
    # for existing callers that construct CoverageAuditResult directly
    # (tests). Production aggregate() always sets these explicitly.
    manifest_state: str = "valid"
    manifest_run_id_matches: bool | None = None


def validate_manifest_coverage(
    manifest: ReaderRecordAskRunManifest | None,
    artifacts: list[Any],
    *,
    manifest_state: str = "valid",
    manifest_run_id_matches: bool | None = None,
) -> CoverageAuditResult:
    """Audit the consistency between ``manifest`` and ``artifacts``.

    See :class:`CoverageAuditResult` for field semantics.

    When ``manifest`` is ``None`` (file did not exist OR corrupt — the
    caller distinguishes via ``manifest_state``), the result has
    ``manifest_present=False``, ``planned_count=0``, ``missing_count=0``,
    ``unexpected_count=0``, ``identity_mismatch_count=0``. Duplicates
    are still detected among artifacts (they are an artifact-side
    problem). ``evaluable_artifact_count`` is
    ``len(artifacts) - duplicate_count``. The caller uses
    ``manifest_present=False`` to block the verdict.

    ``manifest_state`` and ``manifest_run_id_matches`` are propagated
    to the result so the verdict function can distinguish
    absent/corrupt/foreign/valid manifests. Defaults preserve backward
    compatibility; production ``aggregate()`` always sets these
    explicitly.
    """
    # Index artifacts by (case_id, run_index). Track duplicates.
    key_to_artifacts: dict[tuple[str, int], list[Any]] = {}
    for art in artifacts:
        case_id = getattr(art, "case_id", None)
        run_index = getattr(art, "run_index", None)
        if not isinstance(case_id, str) or not isinstance(run_index, int):
            # Malformed artifact — treat as unexpected with a synthetic
            # key so it is excluded from evaluable. We cannot reliably
            # attribute it to a case_id, so skip duplicate counting and
            # let the caller reject via manifest_present / status.
            continue
        key_to_artifacts.setdefault((case_id, run_index), []).append(art)

    duplicate_count = 0
    duplicate_run_indices: dict[str, list[int]] = {}
    for (case_id, run_index), arts in key_to_artifacts.items():
        if len(arts) > 1:
            extra = len(arts) - 1
            duplicate_count += extra
            duplicate_run_indices.setdefault(case_id, []).append(run_index)
    for case_id in duplicate_run_indices:
        duplicate_run_indices[case_id] = sorted(duplicate_run_indices[case_id])

    if manifest is None:
        # When caller indicates corrupt, propagate the state so the
        # verdict function distinguishes absent (never ran) from
        # corrupt (ran but unauditable).
        effective_state = manifest_state if manifest_state != "valid" else "absent"
        return CoverageAuditResult(
            manifest_present=False,
            manifest_status=None,
            planned_count=0,
            completed_count=0,
            missing_count=0,
            duplicate_count=duplicate_count,
            unexpected_count=0,
            identity_mismatch_count=0,
            evaluable_artifact_count=max(
                0, len(artifacts) - duplicate_count
            ),
            dataset_identity=None,
            missing_run_indices={},
            duplicate_run_indices=duplicate_run_indices,
            unexpected_run_indices={},
            manifest_state=effective_state,
            manifest_run_id_matches=None,
        )

    # Manifest present.
    planned_set: set[tuple[str, int]] = set()
    for case_id, indices in manifest.planned_run_indices.items():
        if not isinstance(case_id, str):
            continue
        for ri in indices:
            if isinstance(ri, int):
                planned_set.add((case_id, ri))

    missing_run_indices: dict[str, list[int]] = {}
    missing_count = 0
    for case_id, indices in manifest.planned_run_indices.items():
        if not isinstance(case_id, str):
            continue
        for ri in indices:
            if not isinstance(ri, int):
                continue
            if (case_id, ri) not in key_to_artifacts:
                missing_count += 1
                missing_run_indices.setdefault(case_id, []).append(ri)
    for case_id in missing_run_indices:
        missing_run_indices[case_id] = sorted(missing_run_indices[case_id])

    unexpected_run_indices: dict[str, list[int]] = {}
    unexpected_count = 0
    for (case_id, run_index), arts in key_to_artifacts.items():
        if (case_id, run_index) not in planned_set:
            # All artifacts at this key are unexpected.
            unexpected_count += len(arts)
            unexpected_run_indices.setdefault(case_id, []).append(run_index)
    for case_id in unexpected_run_indices:
        unexpected_run_indices[case_id] = sorted(unexpected_run_indices[case_id])

    manifest_identity = manifest.identity_tuple()
    identity_mismatch_count = 0
    for art in artifacts:
        art_did = getattr(art, "dataset_id", None)
        art_dsv = getattr(art, "dataset_schema_version", None)
        art_sha = getattr(art, "dataset_content_sha256", None)
        if (art_did, art_dsv, art_sha) != manifest_identity:
            identity_mismatch_count += 1

    evaluable = max(
        0,
        len(artifacts)
        - duplicate_count
        - unexpected_count
        - identity_mismatch_count,
    )

    return CoverageAuditResult(
        manifest_present=True,
        manifest_status=manifest.status,
        planned_count=manifest.planned_count,
        completed_count=manifest.completed_count,
        missing_count=missing_count,
        duplicate_count=duplicate_count,
        unexpected_count=unexpected_count,
        identity_mismatch_count=identity_mismatch_count,
        evaluable_artifact_count=evaluable,
        dataset_identity=manifest_identity,
        missing_run_indices=missing_run_indices,
        duplicate_run_indices=duplicate_run_indices,
        unexpected_run_indices=unexpected_run_indices,
        manifest_state=manifest_state,
        manifest_run_id_matches=manifest_run_id_matches,
    )


# ---------------------------------------------------------------------------
# Internal: JSON dict parsing + schema validation
# ---------------------------------------------------------------------------


def _parse_and_validate_manifest_dict(s: str) -> dict[str, Any]:
    """Parse ``s`` as JSON and validate it as a manifest dict.

    Returns the validated dict (suitable for passing to the manifest
    dataclass constructor). Raises :class:`RunManifestError` on any
    validation failure.

    P0-1 strict contract (10 rejection rules). Each rule is checked
    IN ORDER so the fail-closed reason is deterministic. Importantly,
    list-uniqueness is verified BEFORE any set operation — using
    ``set()`` to silently deduplicate ``[0, 0]`` → ``{0}`` before
    validating would allow duplicate-index manifests to masquerade as
    well-formed (the root cause of the minimal reproduction bug).

    Rejection rules:

    1. JSON unparseable / top-level not a dict / missing required field.
    2. ``schema_version`` not a string / does not match
       :data:`MANIFEST_SCHEMA_VERSION`.
    3. ``run_id`` not a non-empty string.
    4. ``phase`` not an int (bool rejected) OR not in {1, 2, 3}.
    5. ``dataset_id`` / ``dataset_schema_version`` not non-empty strings.
    6. ``dataset_content_sha256`` not a string OR not exactly 64
       lowercase hex chars (SHA-256).
    7. ``status`` not in {"completed", "budget_exhausted"}.
    8. ``planned_run_indices`` / ``completed_run_indices`` /
       ``remaining_run_indices`` not dicts of non-empty-str → list[int]:
       - empty case id (``""``) rejected
       - bool run index rejected (``isinstance(True, int)`` is True)
       - **negative run index rejected**
       - **duplicate run index within a single case's list rejected
         (KEY FIX — previously ``set()`` silently deduplicated)**
    9. ``executed_requests`` / ``executed_tokens`` not non-negative ints
       (bool rejected). **Negative counters rejected.**
    10. ``stop_reason`` not in :data:`_ALLOWED_STOP_REASONS`.

    Then :func:`_assert_coverage_invariant` checks the semantic
    invariants (status/stop_reason/remaining consistency, completed ∩
    remaining disjoint, completed/remaining ⊆ planned).
    """
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        raise RunManifestError(reason="corrupt_manifest") from exc

    if not isinstance(data, dict):
        raise RunManifestError(reason="corrupt_manifest")

    # Rule 1: required fields present.
    for field_name in _REQUIRED_FIELDS:
        if field_name not in data:
            raise RunManifestError(reason="corrupt_manifest")

    # Rule 2: schema_version.
    if not isinstance(data["schema_version"], str):
        raise RunManifestError(reason="corrupt_manifest")
    if data["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise RunManifestError(reason="corrupt_manifest")

    # Rule 3: run_id is non-empty str.
    if not isinstance(data["run_id"], str) or not data["run_id"]:
        raise RunManifestError(reason="corrupt_manifest")

    # Rule 4: phase is int (not bool) in {1, 2, 3}.
    phase = data["phase"]
    if not isinstance(phase, int) or isinstance(phase, bool):
        raise RunManifestError(reason="corrupt_manifest")
    if phase not in _ALLOWED_PHASES:
        raise RunManifestError(reason="corrupt_manifest")

    # Rule 5: dataset_id / dataset_schema_version non-empty str.
    if not isinstance(data["dataset_id"], str) or not data["dataset_id"]:
        raise RunManifestError(reason="corrupt_manifest")
    if (
        not isinstance(data["dataset_schema_version"], str)
        or not data["dataset_schema_version"]
    ):
        raise RunManifestError(reason="corrupt_manifest")

    # Rule 6: dataset_content_sha256 is 64 lowercase hex (SHA-256).
    sha = data["dataset_content_sha256"]
    if not isinstance(sha, str):
        raise RunManifestError(reason="corrupt_manifest")
    if not _SHA256_LOWERCASE_HEX_RE.match(sha):
        raise RunManifestError(reason="corrupt_manifest")

    # Rule 7: status in allowlist.
    if data["status"] not in ("completed", "budget_exhausted"):
        raise RunManifestError(reason="corrupt_manifest")

    # Rule 8: planned/completed/remaining are dicts of non-empty-str →
    # list[int (not bool, not negative)] with NO duplicates within a
    # single case's list.
    for fld in (
        "planned_run_indices",
        "completed_run_indices",
        "remaining_run_indices",
    ):
        val = data[fld]
        if not isinstance(val, dict):
            raise RunManifestError(reason="corrupt_manifest")
        for k, v in val.items():
            # Empty case id rejected.
            if not isinstance(k, str) or not k:
                raise RunManifestError(reason="corrupt_manifest")
            if not isinstance(v, list):
                raise RunManifestError(reason="corrupt_manifest")
            seen_in_this_list: set[int] = set()
            for item in v:
                # bool rejected (isinstance(True, int) is True).
                if not isinstance(item, int) or isinstance(item, bool):
                    raise RunManifestError(reason="corrupt_manifest")
                # Negative run index rejected.
                if item < 0:
                    raise RunManifestError(reason="corrupt_manifest")
                # KEY FIX: duplicate within the same list rejected.
                # Previously set() silently deduplicated [0,0] → {0},
                # allowing fake coverage. Verify uniqueness BEFORE any
                # set operation.
                if item in seen_in_this_list:
                    raise RunManifestError(reason="corrupt_manifest")
                seen_in_this_list.add(item)

    # Rule 9: executed_requests / executed_tokens are non-negative ints
    # (bool rejected). Negative counters rejected.
    executed_requests = data["executed_requests"]
    if (
        not isinstance(executed_requests, int)
        or isinstance(executed_requests, bool)
        or executed_requests < 0
    ):
        raise RunManifestError(reason="corrupt_manifest")
    executed_tokens = data["executed_tokens"]
    if (
        not isinstance(executed_tokens, int)
        or isinstance(executed_tokens, bool)
        or executed_tokens < 0
    ):
        raise RunManifestError(reason="corrupt_manifest")

    # Rule 10: stop_reason in allowlist.
    stop_reason = data["stop_reason"]
    if stop_reason not in _ALLOWED_STOP_REASONS:
        raise RunManifestError(reason="corrupt_manifest")

    # R4-A4-2R2 Rules 11-16: optional budget/identity fields. Each is
    # OPTIONAL (defaults applied in ``from_json`` when absent). When
    # PRESENT, strict type validation runs so a corrupt manifest cannot
    # smuggle malformed budget/identity data into the aggregate audit.
    #
    # Rule 11: ``runtime_fixture_identities`` — optional dict of
    # non-empty case_id → 64-char lowercase hex SHA-256. Empty dict is
    # valid (backwards compat for pre-R4-A4-2R2 manifests). When keys
    # are present, values MUST be strict SHA-256 hex (same regex as
    # ``dataset_content_sha256``).
    if "runtime_fixture_identities" in data:
        rfi = data["runtime_fixture_identities"]
        if not isinstance(rfi, dict):
            raise RunManifestError(reason="corrupt_manifest")
        for k, v in rfi.items():
            if not isinstance(k, str) or not k:
                raise RunManifestError(reason="corrupt_manifest")
            if not isinstance(v, str):
                raise RunManifestError(reason="corrupt_manifest")
            if not _SHA256_LOWERCASE_HEX_RE.match(v):
                raise RunManifestError(reason="corrupt_manifest")

    # Rule 12: ``planned_logical_runs`` — optional non-negative int
    # (bool rejected). Default 0 (backwards compat).
    if "planned_logical_runs" in data:
        plr = data["planned_logical_runs"]
        if (
            not isinstance(plr, int)
            or isinstance(plr, bool)
            or plr < 0
        ):
            raise RunManifestError(reason="corrupt_manifest")

    # Rule 13: ``request_cap`` — optional non-negative int OR null.
    # Bool rejected. Default None (backwards compat / unlimited).
    if "request_cap" in data and data["request_cap"] is not None:
        rc = data["request_cap"]
        if (
            not isinstance(rc, int)
            or isinstance(rc, bool)
            or rc < 0
        ):
            raise RunManifestError(reason="corrupt_manifest")

    # Rule 14: ``token_cap`` — optional non-negative int OR null.
    # Bool rejected. Default None (backwards compat / unlimited).
    if "token_cap" in data and data["token_cap"] is not None:
        tc = data["token_cap"]
        if (
            not isinstance(tc, int)
            or isinstance(tc, bool)
            or tc < 0
        ):
            raise RunManifestError(reason="corrupt_manifest")

    # Rule 15: ``retry_policy`` — R4-A4-2R3 changes type to dict.
    # Accept (a) dict (V2 typed contract — keys validated below in
    # V2 strict block) or (b) the legacy string ``"default"`` (V1
    # backwards compat — migrated to ``{}`` in ``from_json``) or
    # (c) absent (defaults to ``{}``). Any other type rejected.
    if "retry_policy" in data:
        rp = data["retry_policy"]
        if isinstance(rp, str):
            # V1 legacy: only the literal ``"default"`` is accepted.
            if rp != "default":
                raise RunManifestError(reason="corrupt_manifest")
            # Migrate to empty dict so the dataclass type stays dict.
            data["retry_policy"] = {}
        elif isinstance(rp, dict):
            # V2 typed contract — full key/type validation deferred to
            # the V2 strict block below (Rule 18). Here we only ensure
            # all values are ints (or bools rejected) so a corrupt
            # dict cannot smuggle arbitrary types into the dataclass.
            for _k, v in rp.items():
                if not isinstance(_k, str) or not _k:
                    raise RunManifestError(reason="corrupt_manifest")
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    raise RunManifestError(reason="corrupt_manifest")
        else:
            raise RunManifestError(reason="corrupt_manifest")

    # Rule 16: ``retry_headroom`` — optional non-negative int OR null.
    # Bool rejected. Default None (backwards compat).
    if "retry_headroom" in data and data["retry_headroom"] is not None:
        rh = data["retry_headroom"]
        if (
            not isinstance(rh, int)
            or isinstance(rh, bool)
            or rh < 0
        ):
            raise RunManifestError(reason="corrupt_manifest")

    # Rule 17 (R4-A4-2R3): ``audit_contract_version`` — optional
    # non-empty str. Must be one of ``_AUDIT_CONTRACT_VERSIONS`` (which
    # includes ``None`` as a sentinel for "field absent"). Any other
    # string rejected (forward / backward incompatibility is fail-
    # closed).
    if "audit_contract_version" in data and data["audit_contract_version"] is not None:
        acv = data["audit_contract_version"]
        if not isinstance(acv, str) or not acv:
            raise RunManifestError(reason="corrupt_manifest")
        if acv not in _AUDIT_CONTRACT_VERSIONS:
            raise RunManifestError(reason="corrupt_manifest")

    # Rule 18 (R4-A4-2R3): V2 strict contract. When
    # ``audit_contract_version == AUDIT_CONTRACT_VERSION_V2``, enforce
    # the full strict identity/budget contract documented on
    # :data:`AUDIT_CONTRACT_VERSION_V2`. A V2 manifest that fails any
    # of these checks is corrupt — there is NO legacy bypass for V2.
    acv_value = data.get("audit_contract_version", None)
    if acv_value == AUDIT_CONTRACT_VERSION_V2:
        # 18a: ``runtime_fixture_identities`` MUST be present and a
        # non-empty dict. (Empty dict is V1-only — V2 requires
        # coverage of every planned case.)
        rfi = data.get("runtime_fixture_identities", None)
        if not isinstance(rfi, dict) or not rfi:
            raise RunManifestError(reason="corrupt_manifest")

        # 18b: identity map keys MUST exactly equal
        # ``planned_run_indices.keys()`` (no missing, no extra).
        planned_keys = set(data["planned_run_indices"].keys())
        identity_keys = set(rfi.keys())
        if planned_keys != identity_keys:
            raise RunManifestError(reason="corrupt_manifest")

        # 18c: each value MUST be a strict 64-char lowercase hex
        # SHA-256 (Rule 11 already validated the type/regex, but
        # defense-in-depth: re-assert non-empty + regex match here).
        for v in rfi.values():
            if not isinstance(v, str) or not _SHA256_LOWERCASE_HEX_RE.match(v):
                raise RunManifestError(reason="corrupt_manifest")

        # 18d: ``planned_logical_runs`` MUST equal ``planned_count``
        # (sum of len of planned_run_indices values). This catches a
        # harness bug where the planner's logical-run count drifts
        # from the manifest's planned-indices count.
        plr = data.get("planned_logical_runs", 0)
        planned_count = sum(
            len(v) for v in data["planned_run_indices"].values()
        )
        if not isinstance(plr, int) or isinstance(plr, bool) or plr != planned_count:
            raise RunManifestError(reason="corrupt_manifest")

        # 18e: ``retry_policy`` MUST be a dict with the required keys
        # ``tool_max_retries`` and ``output_max_retries``, each a
        # non-negative int (bool rejected).
        rp = data.get("retry_policy", None)
        if not isinstance(rp, dict):
            raise RunManifestError(reason="corrupt_manifest")
        for req_key in _RETRY_POLICY_REQUIRED_KEYS:
            if req_key not in rp:
                raise RunManifestError(reason="corrupt_manifest")
            rv = rp[req_key]
            if (
                not isinstance(rv, int)
                or isinstance(rv, bool)
                or rv < 0
            ):
                raise RunManifestError(reason="corrupt_manifest")

        # 18f: ``retry_headroom`` MUST be a non-negative int (NOT
        # null). V2 contracts require an explicit headroom value so
        # the aggregate can audit ``retries_consumed`` without
        # reconstructing from env.
        rh = data.get("retry_headroom", None)
        if (
            not isinstance(rh, int)
            or isinstance(rh, bool)
            or rh < 0
        ):
            raise RunManifestError(reason="corrupt_manifest")

        # 18g: ``request_cap`` MUST be a non-negative int (NOT null).
        # V2 contracts require an explicit cap so the aggregate can
        # audit budget exhaustion without env reconstruction.
        rc = data.get("request_cap", None)
        if (
            not isinstance(rc, int)
            or isinstance(rc, bool)
            or rc < 0
        ):
            raise RunManifestError(reason="corrupt_manifest")

        # 18h: ``retry_headroom`` MUST equal
        # ``request_cap - planned_logical_runs`` (the contract from
        # R4-A4-2R3 P1). Drift indicates a harness bug.
        if rh != rc - plr:
            raise RunManifestError(reason="corrupt_manifest")

    # Semantic invariants (status/stop_reason/remaining consistency,
    # completed ∩ remaining disjoint, completed/remaining ⊆ planned).
    _assert_coverage_invariant(data)
    return data


def _assert_coverage_invariant(data: dict[str, Any]) -> None:
    """Validate the semantic coverage invariants of a manifest dict.

    Raises :class:`RunManifestError` (reason=``corrupt_manifest``) on
    violation. This is the fail-closed gate for hand-edited or
    partially-written manifests.

    P0-1 invariants (checked AFTER list-uniqueness is verified in
    :func:`_parse_and_validate_manifest_dict`, so set operations are
    safe here):

    1. ``status="completed"``:
       - ``remaining_run_indices`` must be empty (all cases).
       - ``stop_reason`` must be ``None``.
       - ``planned == completed`` per case (as sets — uniqueness
         already verified, so set equality is now safe).
    2. ``status="budget_exhausted"``:
       - ``stop_reason`` must be ``"budget_exhausted"``.
       - ``remaining_run_indices`` must be non-empty (at least one
         case has unfinished work).
    3. ``completed ∩ remaining == ∅`` per case (disjoint).
    4. ``completed ∪ remaining == planned`` per case (no extras, no
       missing). This also rejects completed/remaining indices that
       are not in planned.

    The previous implementation used ``set(planned.get(case_id, []))``
    which silently deduplicated ``[0, 0]`` → ``{0}``, allowing a
    manifest with duplicate indices to pass the
    ``planned == completed ∪ remaining`` check. The fix verifies list
    uniqueness upstream (in ``_parse_and_validate_manifest_dict``)
    BEFORE this function runs, so set operations here are safe.
    """
    planned = data["planned_run_indices"]
    completed = data["completed_run_indices"]
    remaining = data["remaining_run_indices"]
    status = data["status"]
    stop_reason = data["stop_reason"]

    # Invariant 1: status="completed" consistency.
    if status == "completed":
        # remaining must be empty.
        for _case_id, indices in remaining.items():
            if indices:
                raise RunManifestError(reason="corrupt_manifest")
        # stop_reason must be None.
        if stop_reason is not None:
            raise RunManifestError(reason="corrupt_manifest")
        # planned == completed per case (as sets — uniqueness verified
        # upstream, so set equality is now safe).
        all_case_ids = set(planned.keys()) | set(completed.keys())
        for case_id in all_case_ids:
            p = set(planned.get(case_id, []))
            c = set(completed.get(case_id, []))
            if p != c:
                raise RunManifestError(reason="corrupt_manifest")
        # No extra cases in completed beyond planned.
        for case_id in completed:
            if case_id not in planned:
                raise RunManifestError(reason="corrupt_manifest")
        # No extra cases in remaining beyond planned (remaining is
        # empty, but defensive).
        for case_id in remaining:
            if case_id not in planned:
                raise RunManifestError(reason="corrupt_manifest")
        return

    # Invariant 2: status="budget_exhausted" consistency.
    if status == "budget_exhausted":
        # stop_reason must be "budget_exhausted".
        if stop_reason != "budget_exhausted":
            raise RunManifestError(reason="corrupt_manifest")
        # remaining must be non-empty (at least one case has unfinished
        # work).
        total_remaining = sum(len(v) for v in remaining.values())
        if total_remaining == 0:
            raise RunManifestError(reason="corrupt_manifest")

    # Invariant 3 + 4 (apply to both statuses; for "completed" this is
    # a no-op since remaining is empty and planned == completed).
    all_case_ids = set(planned.keys()) | set(completed.keys()) | set(remaining.keys())
    for case_id in all_case_ids:
        p = set(planned.get(case_id, []))
        c = set(completed.get(case_id, []))
        r = set(remaining.get(case_id, []))
        # Disjoint: completed ∩ remaining == ∅.
        if c & r:
            raise RunManifestError(reason="corrupt_manifest")
        # Union: completed ∪ remaining == planned (rejects extras AND
        # missing indices).
        if p != (c | r):
            raise RunManifestError(reason="corrupt_manifest")
