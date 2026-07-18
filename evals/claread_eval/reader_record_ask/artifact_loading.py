"""Typed artifact-load audit seam for the R4-A3 aggregate phase.

Spec: `.trae/specs/audit-r4-a3-eval-harness-final-closure/spec.md`
Requirement: Artifact Load Audit (P0-1 final closure — artifact audit
boundary).

Prior to this module, ``_load_artifacts()`` in the runner script did
``warn + continue`` for every failure mode (JSON corruption, schema
validation failure, foreign run_id, non-object JSON). This made
"absent manifest + corrupt artifact file" indistinguishable from
"absent manifest + zero artifact files" — both produced an empty
``artifacts`` list, and the verdict fell to ``blocked_by_real_model_run``
(the "never ran" verdict) instead of ``blocked_incomplete_real_model_run``
(the "ran but unauditable" verdict).

This module provides a typed :class:`ArtifactLoadResult` that carries:

- ``valid_artifacts``: tuple of successfully parsed :class:`RawArtifact`
  instances that match the requested ``run_id``.
- ``discovered_file_count``: total ``*.json`` files found under the
  artifact directory (before any filtering).
- ``invalid_json_count``: files that could not be parsed as JSON or
  whose top-level value was not a dict.
- ``invalid_schema_count``: files whose JSON parsed successfully but
  failed :meth:`RawArtifact.model_validate` (strict schema violation).
- ``foreign_run_id_count``: files whose ``run_id`` field did not match
  the requested ``run_id`` (foreign artifact — written by a different
  run, accidentally mixed into this run's directory).

The result exposes:

- :attr:`invalid_artifact_count`: sum of invalid_json + invalid_schema
  + foreign_run_id.
- :attr:`is_clean`: ``True`` iff ``invalid_artifact_count == 0``.

Safety contract:

- The result NEVER carries exception text, JSON content, file paths,
  or any other potentially sensitive data. Only typed counts and the
  parsed :class:`RawArtifact` instances are exposed.
- A corrupt/invalid/foreign artifact file MUST NOT silently disappear
  — it is counted and forces the aggregate verdict to
  ``blocked_incomplete_real_model_run`` (via
  :func:`run_reader_record_ask_r4_a3._decide_final_verdict`).
- ``absent manifest + 0 discovered files`` is the ONLY combination
  that may yield ``blocked_by_real_model_run``. Any non-zero
  ``discovered_file_count`` with an absent manifest indicates the run
  started but its audit trail is broken → ``blocked_incomplete_real_model_run``.
- Foreign artifacts are NOT classified as dataset identity mismatch
  unless their dataset identity itself mismatches. A foreign run_id
  is an artifact-side audit failure, not a dataset-drift signal.

The strict :class:`RawArtifact` schema (P0-1) is the single parsing
truth — this module does NOT add a parallel validation layer. Every
artifact that lands in ``valid_artifacts`` has passed the full strict
contract (StrictInt / StrictBool / StrictStr / 64-hex SHA / non-empty
string validators).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact


@dataclass(frozen=True)
class ArtifactLoadResult:
    """Typed result of :func:`load_artifacts_with_audit`.

    All counts are non-negative integers. ``valid_artifacts`` is a
    tuple of :class:`RawArtifact` instances that passed the strict
    schema validation AND matched the requested ``run_id``.

    The result is deliberately a frozen dataclass — it cannot be
    mutated after construction, and its fields are all safe to surface
    in the aggregate report (no file paths, no JSON content, no
    exception text).

    Fields:

    - ``valid_artifacts``: tuple of parsed artifacts matching the
      requested ``run_id``. Empty when no files were found OR all
      files were invalid/foreign.
    - ``discovered_file_count``: total ``*.json`` files found under
      ``artifact_dir`` (before any filtering). ``0`` when the directory
      does not exist or contains no ``.json`` files.
    - ``invalid_json_count``: files that could not be parsed as JSON
      (``json.JSONDecodeError``) OR whose top-level value was not a
      dict (e.g. ``"[1, 2, 3]"`` or ``"\"hello\""``).
    - ``invalid_schema_count``: files whose JSON parsed successfully
      but failed :meth:`RawArtifact.model_validate` (strict schema
      violation — e.g. ``run_index=True``, ``budget_exhausted="false"``,
      malformed SHA, missing required field, extra field).
    - ``foreign_run_id_count``: files whose ``run_id`` field did not
      match the requested ``run_id``. These are NOT included in
      ``valid_artifacts`` and NOT classified as schema-invalid — they
      are a separate audit category (foreign artifact mixing).

    Properties:

    - :attr:`invalid_artifact_count`: sum of invalid_json +
      invalid_schema + foreign_run_id.
    - :attr:`is_clean`: ``True`` iff ``invalid_artifact_count == 0``.
      The aggregate verdict gate uses this to decide whether to
      block (``blocked_incomplete_real_model_run``) or proceed to
      the normal verdict path.
    """

    valid_artifacts: tuple[RawArtifact, ...]
    discovered_file_count: int
    invalid_json_count: int
    invalid_schema_count: int
    foreign_run_id_count: int

    @property
    def invalid_artifact_count(self) -> int:
        """Total invalid artifacts (JSON + schema + foreign run_id)."""
        return (
            self.invalid_json_count
            + self.invalid_schema_count
            + self.foreign_run_id_count
        )

    @property
    def is_clean(self) -> bool:
        """``True`` iff no invalid/foreign artifacts were discovered.

        When ``False``, the aggregate verdict MUST fall to
        ``blocked_incomplete_real_model_run`` regardless of how many
        valid artifacts were loaded — the audit trail is broken.
        """
        return self.invalid_artifact_count == 0


def load_artifacts_with_audit(
    artifact_dir: Path,
    run_id: str,
    *,
    _stderr: object = sys.stderr,
) -> ArtifactLoadResult:
    """Load all artifacts under ``artifact_dir/*.json`` for ``run_id``.

    This is the production artifact-load seam — the SINGLE source of
    truth for parsing on-disk artifacts. It enforces the strict
    :class:`RawArtifact` contract (P0-1) via
    :meth:`RawArtifact.model_validate` and produces a typed
    :class:`ArtifactLoadResult` that the aggregate verdict gate
    consults.

    Behavior:

    - ``artifact_dir`` does not exist or contains no ``.json`` files →
      returns ``ArtifactLoadResult(valid_artifacts=(), discovered_file_count=0,
      invalid_json_count=0, invalid_schema_count=0, foreign_run_id_count=0)``.
      The aggregate treats this as "no run evidence" — combined with
      an absent manifest, this yields ``blocked_by_real_model_run``.
    - File cannot be parsed as JSON → ``invalid_json_count`` incremented.
    - JSON parses but top-level value is not a dict → ``invalid_json_count``
      incremented (a non-object JSON cannot be an artifact).
    - JSON parses to a dict but ``run_id`` field does not match the
      requested ``run_id`` → ``foreign_run_id_count`` incremented.
      The file is NOT included in ``valid_artifacts``.
    - JSON parses to a dict with matching ``run_id`` but
      :meth:`RawArtifact.model_validate` fails (strict schema violation)
      → ``invalid_schema_count`` incremented.
    - Otherwise the artifact is appended to ``valid_artifacts``.

    Safety:

    - Exception text, JSON content, and file paths are NEVER carried
      in the result — only typed counts. A single
      ``exception_type=<ClassName>`` is printed to stderr for operator
      diagnostics, but the report surfaces only the count.
    - The result is a frozen dataclass — callers cannot mutate it.

    Args:
        artifact_dir: directory containing ``*.json`` artifact files.
        run_id: the run id to filter by (foreign run_ids are counted
            separately, NOT silently dropped).
        _stderr: stderr stream for diagnostic warnings (defaults to
            ``sys.stderr``; injected for testability).

    Returns:
        :class:`ArtifactLoadResult` with typed counts and parsed
        artifacts.
    """
    from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact

    if not artifact_dir.is_dir():
        # Directory does not exist — no files to discover. This is the
        # normal "never ran" path: absent manifest + 0 discovered files
        # → blocked_by_real_model_run.
        return ArtifactLoadResult(
            valid_artifacts=(),
            discovered_file_count=0,
            invalid_json_count=0,
            invalid_schema_count=0,
            foreign_run_id_count=0,
        )

    valid_artifacts: list[RawArtifact] = []
    discovered = 0
    invalid_json = 0
    invalid_schema = 0
    foreign_run_id = 0

    for json_path in sorted(artifact_dir.glob("*.json")):
        discovered += 1
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, UnicodeError, json.JSONDecodeError) as exc:
            # JSON corruption OR UTF-8 decoding failure — count and skip.
            # This catches:
            # - ``OSError``: file vanished / unreadable between glob and read.
            # - ``UnicodeDecodeError`` / ``UnicodeError``: invalid UTF-8 bytes,
            #   truncated multi-byte sequence, binary content. Previously these
            #   propagated out of ``read_text(encoding="utf-8")`` and crashed
            #   the entire aggregate — now they are classified as
            #   ``invalid_json_count`` so the verdict falls to
            #   ``blocked_incomplete_real_model_run`` instead of raising.
            # - ``json.JSONDecodeError``: malformed JSON syntax.
            # Do NOT carry the exception text in the result; only the
            # typed count. A single ``exception_type=<ClassName>`` is
            # printed to stderr for operator diagnostics.
            print(
                f"WARN: failed to parse artifact JSON: "
                f"exception_type={type(exc).__name__}",
                file=_stderr,  # type: ignore[arg-type]
            )
            invalid_json += 1
            continue

        # Non-object JSON (e.g. ``"[1,2,3]"`` or ``"\"hello\""``)
        # cannot be an artifact — count as invalid JSON.
        if not isinstance(payload, dict):
            print(
                "WARN: artifact JSON top-level is not an object; "
                "counting as invalid_json",
                file=_stderr,  # type: ignore[arg-type]
            )
            invalid_json += 1
            continue

        # Foreign run_id filter. The file exists and parses, but its
        # run_id does not match the requested run — count separately
        # so the aggregate can distinguish "corrupt schema" from
        # "foreign artifact mixing". Do NOT include in valid_artifacts.
        payload_run_id = payload.get("run_id")
        if not isinstance(payload_run_id, str) or payload_run_id != run_id:
            print(
                "WARN: artifact run_id does not match requested run_id; "
                "counting as foreign_run_id",
                file=_stderr,  # type: ignore[arg-type]
            )
            foreign_run_id += 1
            continue

        # Strict schema validation via RawArtifact.model_validate.
        # This enforces the P0-1 contract (StrictInt/Bool/Str + format
        # validators). A ValidationError here means the file is
        # corrupt or hand-edited — count as invalid_schema and skip.
        try:
            artifact = RawArtifact.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARN: artifact schema validation failed: "
                f"exception_type={type(exc).__name__}",
                file=_stderr,  # type: ignore[arg-type]
            )
            invalid_schema += 1
            continue

        valid_artifacts.append(artifact)

    return ArtifactLoadResult(
        valid_artifacts=tuple(valid_artifacts),
        discovered_file_count=discovered,
        invalid_json_count=invalid_json,
        invalid_schema_count=invalid_schema,
        foreign_run_id_count=foreign_run_id,
    )
