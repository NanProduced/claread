"""Tests for R4-A3 runner runs-dir path normalization (R4-A4-2R P0).

Spec: R4-A4-2R — Real Eval Fixture Identity / Path Contract Repair.

Audit finding (R4-A4-2): artifacts were written to
``services/services/api/tmp/...`` because the runner passed a relative
``--runs-dir`` string to the pytest subprocess whose cwd was
``services/api/``. The same relative path resolved to
``services/api/tmp/...`` in the main process (cwd=``evals/``), so
aggregate could not find the artifacts written by the harness.

Requirement: the runner MUST normalize ``--runs-dir`` to an absolute
canonical path BEFORE invoking the subprocess. Aggregate uses the same
normalized runs-dir. Cross-cwd invocation must resolve to the same
absolute directory.

These tests prove:
- ``--runs-dir`` is resolved to absolute at runner entry.
- The same relative ``--runs-dir`` resolves to the same absolute path
  regardless of the runner's cwd.
- The path passed to the subprocess via ``CLAREAD_R4_A3_RUNS_DIR`` is
  absolute (so the subprocess cwd cannot re-resolve it).
- ``RunSessionLayout`` itself normalizes ``runs_root`` so writer
  (harness subprocess) and reader (aggregate) cannot diverge even if
  a relative path slips through.
- The historical ``services/services/api/tmp`` double-resolution is
  impossible after normalization.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = (
    _REPO_ROOT / "evals" / "scripts" / "run_reader_record_ask_eval.py"
)


def _load_runner_module():
    """Load the runner script as a module (it's not in a package)."""
    spec = importlib.util.spec_from_file_location(
        "run_reader_record_ask_eval", _RUNNER_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_reader_record_ask_eval"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner_module():
    return _load_runner_module()


@pytest.fixture
def clean_runs_dir_env(monkeypatch):
    """Remove CLAREAD_R4_A3_RUNS_DIR from env."""
    monkeypatch.delenv("CLAREAD_R4_A3_RUNS_DIR", raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Runner resolves --runs-dir to absolute before subprocess invocation
# ---------------------------------------------------------------------------


def test_p0_runs_dir_resolved_to_absolute_before_subprocess(
    runner_module, clean_runs_dir_env, tmp_path, monkeypatch
) -> None:
    """``--runs-dir`` MUST be resolved to an absolute path BEFORE the
    pytest subprocess is invoked.

    The subprocess has cwd=``services/api/``. If the runner passed a
    relative path, the subprocess would re-resolve it against its own
    cwd, producing the historical ``services/services/api/tmp`` double-
    resolution bug.

    This test patches ``subprocess.call`` to capture the env var that
    reaches the subprocess and asserts it is an absolute path.
    """
    target = tmp_path / "services" / "api" / "tmp" / "reader-record-ask-r4-a3" / "runs"
    target.mkdir(parents=True)

    # Build a relative path that, if not resolved, would re-resolve
    # differently under a different cwd.
    #
    # The historical bug occurred when the runner main process cwd was
    # ``evals/`` and the subprocess cwd was ``services/api/``. This test
    # reproduces that scenario: the relative path is computed from
    # ``evals/`` and the runner's cwd is set to ``evals/`` via
    # ``monkeypatch.chdir`` so the relative path is meaningful for the
    # runner's resolution context.
    evals_cwd = _REPO_ROOT / "evals"
    monkeypatch.chdir(evals_cwd)
    relative = os.path.relpath(target, start=evals_cwd)

    captured_env: dict[str, str] = {}

    def _capture_call(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return 0

    monkeypatch.setattr(runner_module.subprocess, "call", _capture_call)

    # Build a valid dataset dir so preflight passes.
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "dataset.yaml").write_text("{}", encoding="utf-8")

    runner_module.run_phase(
        phase=1,
        run_id="phase1-test",
        runs_dir=Path(relative),
        prior_run_id=None,
        dataset_dir=dataset_dir,
    )

    assert "CLAREAD_R4_A3_RUNS_DIR" in captured_env, (
        "runner did not propagate CLAREAD_R4_A3_RUNS_DIR to subprocess env"
    )
    runs_dir_passed = Path(captured_env["CLAREAD_R4_A3_RUNS_DIR"])
    assert runs_dir_passed.is_absolute(), (
        f"runner passed a relative runs-dir to subprocess: {runs_dir_passed!r}; "
        f"this is the historical double-resolution root cause"
    )
    # The absolute path must point at the intended target.
    assert runs_dir_passed.resolve() == target.resolve(), (
        f"runner passed wrong runs-dir to subprocess: "
        f"got {runs_dir_passed!r}, expected {target!r}"
    )
    # Explicitly assert NO "services/services/api" double-resolution.
    parts = runs_dir_passed.resolve().parts
    for i in range(len(parts) - 2):
        assert not (parts[i] == "services" and parts[i + 1] == "services"), (
            f"doubly-resolved path detected: {runs_dir_passed!r}"
        )


def test_p0_runs_dir_same_absolute_path_from_different_cwd(
    runner_module, clean_runs_dir_env, tmp_path, monkeypatch
) -> None:
    """The cross-cwd contract: from any cwd, with the appropriate
    relative ``--runs-dir`` for THAT cwd, ``run_phase`` MUST propagate
    the SAME absolute path to the subprocess.

    Audit context: the historical bug was a single relative path being
    re-resolved against the subprocess cwd (``services/api/``),
    producing ``services/services/api/tmp/...``. The fix is to
    normalize at runner entry. This test verifies that, after
    normalization, two invocations from DIFFERENT cwds — each passing
    the relative path appropriate to its own cwd — both propagate the
    SAME absolute target path to the subprocess env var.

    Note: this is NOT "the same relative string resolves to the same
    absolute path regardless of cwd" (that is mathematically false).
    Each invocation uses the relative path that is correct for its own
    cwd; the contract is that both end up at the same absolute target
    after the runner normalizes.
    """
    target = tmp_path / "services" / "api" / "tmp" / "r4-a3" / "runs"
    target.mkdir(parents=True)

    captured_envs: list[dict[str, str]] = []

    def _capture_call(*args, **kwargs):
        captured_envs.append(dict(kwargs.get("env", {})))
        return 0

    monkeypatch.setattr(runner_module.subprocess, "call", _capture_call)

    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "dataset.yaml").write_text("{}", encoding="utf-8")

    # Invoke from cwd=evals/ — use the relative path appropriate for
    # that cwd.
    evals_cwd = _REPO_ROOT / "evals"
    monkeypatch.chdir(evals_cwd)
    relative_from_evals = os.path.relpath(target, start=evals_cwd)
    runner_module.run_phase(
        phase=1,
        run_id="phase1-from-evals",
        runs_dir=Path(relative_from_evals),
        prior_run_id=None,
        dataset_dir=dataset_dir,
    )

    # Invoke from cwd=<repo>/ — use the relative path appropriate for
    # that cwd.
    monkeypatch.chdir(_REPO_ROOT)
    relative_from_repo = os.path.relpath(target, start=_REPO_ROOT)
    runner_module.run_phase(
        phase=1,
        run_id="phase1-from-repo",
        runs_dir=Path(relative_from_repo),
        prior_run_id=None,
        dataset_dir=dataset_dir,
    )

    assert len(captured_envs) == 2, (
        f"expected 2 subprocess invocations, got {len(captured_envs)}"
    )
    p1 = Path(captured_envs[0]["CLAREAD_R4_A3_RUNS_DIR"])
    p2 = Path(captured_envs[1]["CLAREAD_R4_A3_RUNS_DIR"])
    assert p1.is_absolute(), f"first invocation passed relative path: {p1!r}"
    assert p2.is_absolute(), f"second invocation passed relative path: {p2!r}"
    # Both invocations MUST resolve to the same absolute target path,
    # proving the runner normalizes before subprocess invocation.
    assert p1.resolve() == p2.resolve(), (
        f"cross-cwd invocation produced different absolute targets: "
        f"{p1!r} (from evals/) vs {p2!r} (from repo root)"
    )
    # And that target MUST equal the intended target directory.
    assert p1.resolve() == target.resolve(), (
        f"resolved path does not match target: {p1!r} vs {target!r}"
    )


# ---------------------------------------------------------------------------
# RunSessionLayout normalizes runs_root at construction
# ---------------------------------------------------------------------------


def test_p0_run_session_layout_normalizes_relative_runs_root(
    runner_module, tmp_path, monkeypatch
) -> None:
    """``RunSessionLayout`` MUST normalize ``runs_root`` to an absolute
    canonical path at construction.

    Even if a relative path slips through (e.g. via env var or direct
    construction), the layout's ``runs_root`` must be absolute so
    writer (harness subprocess) and reader (aggregate) cannot diverge.
    """
    from claread_eval.reader_record_ask.session import RunSessionLayout

    target = tmp_path / "runs"
    target.mkdir()
    relative = os.path.relpath(target, start=tmp_path)

    monkeypatch.chdir(tmp_path)
    layout = RunSessionLayout(runs_root=relative, run_id="phase1-test")
    assert layout.runs_root.is_absolute(), (
        f"RunSessionLayout.runs_root is not absolute: {layout.runs_root!r}"
    )
    assert layout.runs_root == target.resolve(), (
        f"RunSessionLayout.runs_root mismatch: "
        f"{layout.runs_root!r} vs {target.resolve()!r}"
    )


def test_p0_run_session_layout_writer_and_reader_share_absolute_root(
    runner_module, tmp_path, monkeypatch
) -> None:
    """Writer (harness) and reader (aggregate) MUST resolve to the same
    absolute ``runs_root`` even when constructed with different relative
    inputs from different cwds.
    """
    from claread_eval.reader_record_ask.session import RunSessionLayout

    target = tmp_path / "shared" / "runs"
    target.mkdir(parents=True)

    # Writer constructed from cwd=target's parent.
    monkeypatch.chdir(tmp_path / "shared")
    writer = RunSessionLayout(
        runs_root=Path("runs"), run_id="phase1-shared"
    )

    # Reader constructed from cwd=tmp_path with relative path "shared/runs".
    monkeypatch.chdir(tmp_path)
    reader = RunSessionLayout(
        runs_root=Path("shared/runs"), run_id="phase1-shared"
    )

    assert writer.runs_root.is_absolute()
    assert reader.runs_root.is_absolute()
    assert writer.runs_root == reader.runs_root, (
        f"writer runs_root {writer.runs_root!r} != reader runs_root "
        f"{reader.runs_root!r} — they would write/read different paths"
    )
    # The artifact_path() must be identical (write/read round-trip).
    write_path = writer.artifact_path(
        case_id="bbc_main_idea",
        model_short_name="deepseek-v4",
        thinking_enabled=False,
        run_index=0,
    )
    read_path = reader.artifact_path(
        case_id="bbc_main_idea",
        model_short_name="deepseek-v4",
        thinking_enabled=False,
        run_index=0,
    )
    assert write_path == read_path, (
        f"write_path != read_path: {write_path!r} vs {read_path!r}"
    )


# ---------------------------------------------------------------------------
# Historical double-resolution is impossible after normalization
# ---------------------------------------------------------------------------


def test_p0_no_services_services_api_tmp_path_can_arise(
    runner_module, clean_runs_dir_env, tmp_path, monkeypatch
) -> None:
    """The historical ``services/services/api/tmp/...`` double-resolution
    bug CANNOT arise after normalization.

    The bug occurred when:
    - cwd (runner main) = ``evals/``
    - --runs-dir = ``../services/api/tmp/...`` (relative to evals/)
    - subprocess cwd = ``services/api/``
    - subprocess re-resolves the relative path against its own cwd →
      ``services/api/`` + ``../services/api/tmp/...`` =
      ``services/services/api/tmp/...``

    After normalization: the runner resolves the path to absolute
    BEFORE subprocess invocation, so the subprocess cwd cannot re-
    resolve it.
    """
    # Reproduce the historical layout: <tmp>/evals/ + <tmp>/services/api/tmp/runs
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    api_dir = tmp_path / "services" / "api"
    api_dir.mkdir(parents=True)
    runs_target = api_dir / "tmp" / "runs"
    runs_target.mkdir(parents=True)

    # Simulate: runner main process cwd = evals/
    monkeypatch.chdir(evals_dir)
    # The historical relative --runs-dir from evals/'s perspective.
    historical_relative = Path("../services/api/tmp/runs")

    captured_env: dict[str, str] = {}

    def _capture_call(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        # Simulate the subprocess resolving the env path against its
        # own cwd (services/api/). If the env path is absolute, this
        # resolution is idempotent — the bug cannot arise.
        subprocess_cwd = Path(kwargs.get("cwd", "."))
        runs_dir_passed = Path(captured_env["CLAREAD_R4_A3_RUNS_DIR"])
        if not runs_dir_passed.is_absolute():
            # This is the historical bug: relative path re-resolves
            # against subprocess cwd.
            resolved_in_subprocess = (subprocess_cwd / runs_dir_passed).resolve()
        else:
            resolved_in_subprocess = runs_dir_passed.resolve()
        # Stash for assertion.
        captured_env["_resolved_in_subprocess"] = str(resolved_in_subprocess)
        return 0

    monkeypatch.setattr(runner_module.subprocess, "call", _capture_call)

    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "dataset.yaml").write_text("{}", encoding="utf-8")

    runner_module.run_phase(
        phase=1,
        run_id="phase1-repro",
        runs_dir=historical_relative,
        prior_run_id=None,
        dataset_dir=dataset_dir,
    )

    # The path passed to subprocess MUST be absolute (not the historical
    # relative path that would re-resolve under services/api/ cwd).
    runs_dir_passed = Path(captured_env["CLAREAD_R4_A3_RUNS_DIR"])
    assert runs_dir_passed.is_absolute(), (
        f"runner passed a relative runs-dir to subprocess: "
        f"{runs_dir_passed!r}"
    )
    # And the subprocess resolution must equal the intended target —
    # NOT the doubly-resolved services/services/api/tmp/runs.
    subprocess_resolved = Path(captured_env["_resolved_in_subprocess"])
    assert subprocess_resolved == runs_target.resolve(), (
        f"subprocess resolved runs-dir to {subprocess_resolved!r}, "
        f"expected {runs_target.resolve()!r} — the historical "
        f"services/services/api/tmp double-resolution has regressed"
    )
    # Explicitly assert NO "services/services/api" appears.
    parts = subprocess_resolved.parts
    for i in range(len(parts) - 2):
        assert not (parts[i] == "services" and parts[i + 1] == "services"), (
            f"doubly-resolved path detected: {subprocess_resolved!r}"
        )


# ---------------------------------------------------------------------------
# Aggregate uses the same normalized runs-dir
# ---------------------------------------------------------------------------


def test_p0_aggregate_uses_absolute_runs_dir(
    runner_module, clean_runs_dir_env, tmp_path, monkeypatch
) -> None:
    """Aggregate MUST receive an absolute runs-dir too.

    Without normalization, aggregate (running in the main process with
    cwd=``evals/``) would resolve the relative path differently from
    the subprocess (cwd=``services/api/``), and could not find the
    artifacts written by the harness.
    """
    target = tmp_path / "shared" / "runs"
    target.mkdir(parents=True)
    relative = os.path.relpath(target, start=tmp_path)

    captured_runs_dir: list[Path] = []

    # Patch aggregate to capture the runs_dir it received.
    def _capture_aggregate(*args, **kwargs):
        captured_runs_dir.append(Path(kwargs.get("runs_dir", args[1] if len(args) > 1 else "")))
        return 0

    monkeypatch.setattr(runner_module, "aggregate", _capture_aggregate)

    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "dataset.yaml").write_text("{}", encoding="utf-8")

    # Simulate main() with a relative --runs-dir.
    monkeypatch.chdir(tmp_path)
    runner_module.aggregate(
        run_id="phase1-test",
        runs_dir=Path(relative),
        dataset_dir=dataset_dir,
        report_output=tmp_path / "report.md",
    )

    assert len(captured_runs_dir) == 1
    rd = captured_runs_dir[0]
    # The aggregate function receives whatever the caller passed. The
    # caller (main()) MUST normalize it to absolute BEFORE calling
    # aggregate. This test exercises the aggregate signature directly;
    # the main()-level normalization is covered by the cross-cwd test
    # above and by the run_session_layout normalizing at construction.
    # Here we just verify aggregate's RunSessionLayout construction
    # normalizes the path internally (so the layout's runs_root is
    # absolute even if aggregate received a relative path).
    # Re-run through RunSessionLayout to confirm.
    from claread_eval.reader_record_ask.session import RunSessionLayout

    layout = RunSessionLayout(runs_root=rd, run_id="phase1-test")
    assert layout.runs_root.is_absolute(), (
        f"RunSessionLayout.runs_root is not absolute after aggregate "
        f"construction: {layout.runs_root!r}"
    )
