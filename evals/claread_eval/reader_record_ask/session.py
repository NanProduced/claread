"""RunSessionLayout — single source of truth for run/session/artifact paths.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirement: RunSessionLayout 深模块（P0-1）.

Prior to this module, the harness hardcoded `phase{N}-<ts>` run ids and wrote
artifacts to ``runs/`` root, while the runner read from ``runs/<run_id>/`` and
Phase 2/3 scanned the root directory to guess the latest run. That contract
was broken — run id was not a single source of truth, and stages could not
be connected reliably.

This module exposes a small interface (``run_id`` / ``prior_run_id`` /
``run_dir`` / ``artifact_dir`` / ``artifact_path``) over a robust
implementation: path-traversal fail-closed, deterministic artifact filenames
that do not collide on (case, model, thinking, run_index), and a single
resolver shared by the harness (write), Phase 2/3 (read prior) and aggregate
(read same run).

The module never reads or writes file contents — it only resolves paths.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# Safe identifier chars only. ``..`` / ``/`` / ``\`` / spaces / unicode are
# all rejected so a (prior_)run_id can never escape ``runs_root``.
_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Same rule applied to filename components derived from case_id / model.
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Env var names. The harness reads these (no longer hardcodes run ids).
ENV_RUN_ID = "CLAREAD_R4_A3_RUN_ID"
ENV_PRIOR_RUN_ID = "CLAREAD_R4_A3_PRIOR_RUN_ID"

# Subdirectory under ``run_dir`` that holds per-(case, model, run_index)
# artifacts. Kept as a constant so writer and reader agree.
_ARTIFACT_SUBDIR = "artifacts"

# Filename for the run-level completion manifest (see
# ``run_manifest.ReaderRecordAskRunManifest``). Kept as a constant so
# the harness (writer) and aggregate (reader) agree on the path.
_MANIFEST_FILENAME = "manifest.json"


class RunSessionLayoutError(ValueError):
    """Raised when a run_id / prior_run_id / token fails validation.

    Subclasses ``ValueError`` so callers that already catch ``ValueError``
    for "bad CLI argument" continue to work; the subclass lets tests assert
    the fail-closed path explicitly.
    """


class RunSessionLayout:
    """Single source of truth for run / artifact paths.

    Construct with ``(runs_root, run_id, prior_run_id=None)`` then read
    ``run_dir`` / ``artifact_dir`` / ``prior_artifact_dir`` /
    ``artifact_path(...)``. Construction validates both ids fail-closed.

    The layout never creates directories or writes files — callers (harness,
    runner) own IO. This keeps the module pure and easy to test.
    """

    __slots__ = ("_runs_root", "_run_id", "_prior_run_id")

    def __init__(
        self,
        runs_root: str | Path,
        run_id: str,
        prior_run_id: str | None = None,
    ) -> None:
        self._runs_root = Path(runs_root)
        self._run_id = run_id
        self._prior_run_id = prior_run_id
        self.validate()

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def runs_root(self) -> Path:
        return self._runs_root

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def prior_run_id(self) -> str | None:
        return self._prior_run_id

    @property
    def run_dir(self) -> Path:
        """Directory for this run — ``<runs_root>/<run_id>``."""
        return self._runs_root / self._run_id

    @property
    def artifact_dir(self) -> Path:
        """Directory holding this run's artifacts — ``<run_dir>/artifacts``."""
        return self.run_dir / _ARTIFACT_SUBDIR

    @property
    def prior_artifact_dir(self) -> Path | None:
        """Directory holding the prior run's artifacts.

        Returns ``None`` when ``prior_run_id`` is ``None`` (Phase 1, or
        aggregate on a single phase).
        """
        if self._prior_run_id is None:
            return None
        return self._runs_root / self._prior_run_id / _ARTIFACT_SUBDIR

    @property
    def manifest_path(self) -> Path:
        """Path to this run's manifest file — ``<run_dir>/manifest.json``.

        Single source of truth for the manifest path. Both the harness
        (writer) and aggregate (reader) MUST use this resolver — they
        MUST NOT hand-build the path.
        """
        return self.run_dir / _MANIFEST_FILENAME

    # ------------------------------------------------------------------
    # Artifact filename resolver
    # ------------------------------------------------------------------

    def artifact_path(
        self,
        case_id: str,
        model_short_name: str | None,
        thinking_enabled: bool,
        run_index: int,
    ) -> Path:
        """Resolve the artifact JSON path for one (case, model, run_index).

        Filename format: ``<case_id>__<model|none>__<thinking|none>__<NN>.json``
        where ``NN`` is a 3-digit zero-padded run_index. All four dimensions
        are part of the filename so two independent repetitions of the same
        case never overwrite each other, and a Phase 2 re-run of the same
        case with thinking enabled is distinguishable from the Phase 1
        artifact that produced it.
        """
        _validate_token("case_id", case_id)
        if run_index < 0:
            raise RunSessionLayoutError(
                f"run_index must be >= 0, got {run_index}"
            )
        if run_index > 999:
            raise RunSessionLayoutError(
                f"run_index must be <= 999, got {run_index} (3-digit filename "
                "would collide)"
            )

        model_token = _safe_token_or_none("model_short_name", model_short_name)
        thinking_token = "thinking" if thinking_enabled else "nothinking"
        return (
            self.artifact_dir
            / f"{case_id}__{model_token}__{thinking_token}__{run_index:03d}.json"
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Fail-closed validation of run_id / prior_run_id.

        Raises :class:`RunSessionLayoutError` (a ``ValueError`` subclass)
        if any id contains characters outside ``[A-Za-z0-9_-]`` or could
        escape ``runs_root``. No directories are created.
        """
        _validate_run_id("run_id", self._run_id)
        if self._prior_run_id is not None:
            _validate_run_id("prior_run_id", self._prior_run_id)

        # Defensive: ensure resolved run_dir / artifact_dir are actually
        # inside runs_root. With the regex above this is guaranteed, but the
        # check is cheap and protects against future regressions.
        _assert_inside_root(self._runs_root, self.run_dir, "run_dir")
        _assert_inside_root(self._runs_root, self.artifact_dir, "artifact_dir")
        if self.prior_artifact_dir is not None:
            _assert_inside_root(
                self._runs_root, self.prior_artifact_dir, "prior_artifact_dir"
            )

    # ------------------------------------------------------------------
    # Env-based construction
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        runs_root: str | Path,
        env: Mapping[str, str] | None = None,
    ) -> RunSessionLayout:
        """Build a layout from ``CLAREAD_R4_A3_RUN_ID`` / ``CLAREAD_R4_A3_PRIOR_RUN_ID``.

        ``env`` defaults to ``os.environ``. Raises
        :class:`RunSessionLayoutError` if ``CLAREAD_R4_A3_RUN_ID`` is missing
        or empty — the harness must always have an explicit run id, never a
        guessed "latest run".
        """
        env_map = os.environ if env is None else env
        run_id = env_map.get(ENV_RUN_ID, "").strip()
        if not run_id:
            raise RunSessionLayoutError(
                f"{ENV_RUN_ID} must be set and non-empty — the harness must "
                "receive an explicit run id, never a guessed 'latest run'."
            )
        prior_run_id = env_map.get(ENV_PRIOR_RUN_ID, "").strip() or None
        return cls(runs_root=runs_root, run_id=run_id, prior_run_id=prior_run_id)

    # ------------------------------------------------------------------
    # Dunder helpers (for logging / tests)
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"RunSessionLayout(run_id={self._run_id!r}, "
            f"prior_run_id={self._prior_run_id!r}, "
            f"runs_root={str(self._runs_root)!r})"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_run_id(field: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise RunSessionLayoutError(
            f"{field} must be a non-empty string, got {value!r}"
        )
    if not _SAFE_RUN_ID_RE.match(value):
        raise RunSessionLayoutError(
            f"{field} must match ^[A-Za-z0-9_-]+$ (path-traversal fail-closed); "
            f"got {value!r}"
        )


def _validate_token(field: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise RunSessionLayoutError(
            f"{field} must be a non-empty string, got {value!r}"
        )
    if not _SAFE_TOKEN_RE.match(value):
        raise RunSessionLayoutError(
            f"{field} must match ^[A-Za-z0-9_.-]+$ (filename safety); "
            f"got {value!r}"
        )


def _safe_token_or_none(field: str, value: str | None) -> str:
    if value is None:
        return "none"
    _validate_token(field, value)
    return value


def _assert_inside_root(root: Path, candidate: Path, label: str) -> None:
    """Defensive: ``candidate`` must resolve to a path inside ``root``."""
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise RunSessionLayoutError(
            f"{label} ({candidate_resolved}) escapes runs_root ({root_resolved})"
        ) from exc
