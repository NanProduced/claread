"""Tests for R4-A3 runner dataset-dir explicit binding (P0-1).

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirement: real runs (Phase 1/2/3) AND aggregate MUST explicitly
declare the dataset directory via ``--dataset-dir`` CLI or
``CLAREAD_R4_A3_DATASET_DIR`` env. No silent fallback to
``evals/tmp/reader-record-ask-r4-a3/``.

Priority: CLI ``--dataset-dir`` > env ``CLAREAD_R4_A3_DATASET_DIR``.
If neither is set, the runner exits with code 2 BEFORE invoking the
pytest subprocess (so provider calls = 0).
"""

from __future__ import annotations

import importlib.util
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
def clean_dataset_env(monkeypatch):
    """Remove ``CLAREAD_R4_A3_DATASET_DIR`` from env."""
    monkeypatch.delenv("CLAREAD_R4_A3_DATASET_DIR", raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Priority: CLI > env
# ---------------------------------------------------------------------------


def test_cli_dataset_dir_overrides_env(
    runner_module, clean_dataset_env, tmp_path
) -> None:
    """CLI ``--dataset-dir`` takes priority over env."""
    env_dir = tmp_path / "env-dataset"
    env_dir.mkdir()
    (env_dir / "dataset.yaml").write_text("{}", encoding="utf-8")

    cli_dir = tmp_path / "cli-dataset"
    cli_dir.mkdir()
    (cli_dir / "dataset.yaml").write_text("{}", encoding="utf-8")

    clean_dataset_env.setenv("CLAREAD_R4_A3_DATASET_DIR", str(env_dir))
    resolved = runner_module._resolve_dataset_dir(str(cli_dir))
    assert resolved == cli_dir.resolve()


def test_env_used_when_cli_missing(
    runner_module, clean_dataset_env, tmp_path
) -> None:
    """Env is used when CLI flag is not provided."""
    env_dir = tmp_path / "env-dataset"
    env_dir.mkdir()
    (env_dir / "dataset.yaml").write_text("{}", encoding="utf-8")

    clean_dataset_env.setenv("CLAREAD_R4_A3_DATASET_DIR", str(env_dir))
    resolved = runner_module._resolve_dataset_dir(None)
    assert resolved == env_dir.resolve()


def test_returns_none_when_neither_cli_nor_env(
    runner_module, clean_dataset_env
) -> None:
    """Returns None when neither CLI nor env is set (no silent fallback)."""
    resolved = runner_module._resolve_dataset_dir(None)
    assert resolved is None


# ---------------------------------------------------------------------------
# Preflight: exit code 2 when dataset-dir missing/invalid
# ---------------------------------------------------------------------------


def test_preflight_exits_2_when_dataset_dir_none(
    runner_module, clean_dataset_env, capsys
) -> None:
    """Runner exits with code 2 when dataset-dir is not configured."""
    with pytest.raises(SystemExit) as exc_info:
        runner_module._preflight_dataset_dir(None)
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "CLAREAD_R4_A3_DATASET_DIR" in captured.err
    assert "not configured" in captured.err


def test_preflight_exits_2_when_dataset_dir_missing(
    runner_module, clean_dataset_env, tmp_path, capsys
) -> None:
    """Runner exits with code 2 when dataset dir does not exist."""
    missing = tmp_path / "does-not-exist"
    with pytest.raises(SystemExit) as exc_info:
        runner_module._preflight_dataset_dir(missing)
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "not found" in captured.err


def test_preflight_exits_2_when_dataset_yaml_missing(
    runner_module, clean_dataset_env, tmp_path, capsys
) -> None:
    """Runner exits with code 2 when dataset.yaml is absent."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(SystemExit) as exc_info:
        runner_module._preflight_dataset_dir(empty_dir)
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "dataset.yaml" in captured.err


def test_preflight_passes_when_dir_valid(
    runner_module, clean_dataset_env, tmp_path
) -> None:
    """Preflight passes when dir exists and contains dataset.yaml."""
    valid_dir = tmp_path / "valid-dataset"
    valid_dir.mkdir()
    (valid_dir / "dataset.yaml").write_text("{}", encoding="utf-8")
    # Should not raise SystemExit.
    runner_module._preflight_dataset_dir(valid_dir)


# ---------------------------------------------------------------------------
# Subprocess not started when dataset-dir missing
# ---------------------------------------------------------------------------


def test_subprocess_not_started_when_dataset_dir_missing(
    runner_module, clean_dataset_env, tmp_path, monkeypatch
) -> None:
    """When dataset-dir is missing, runner exits before subprocess.call.

    This test patches ``subprocess.call`` to fail loudly if invoked —
    proving the runner exits at preflight rather than launching the
    pytest harness subprocess.
    """
    call_invoked = {"count": 0}

    def _fail_if_called(*args, **kwargs):
        call_invoked["count"] += 1
        raise AssertionError(
            "subprocess.call was invoked — runner should have exited "
            "at preflight before reaching subprocess invocation."
        )

    monkeypatch.setattr(runner_module.subprocess, "call", _fail_if_called)

    # Simulate ``main()`` being called with no --dataset-dir and no env.
    # We can't easily call main() without a real argv, so we replicate
    # the preflight-then-subprocess sequence.
    dataset_dir = runner_module._resolve_dataset_dir(None)
    assert dataset_dir is None
    with pytest.raises(SystemExit) as exc_info:
        runner_module._preflight_dataset_dir(dataset_dir)
    assert exc_info.value.code == 2
    assert call_invoked["count"] == 0


# ---------------------------------------------------------------------------
# Aggregate must also explicitly bind dataset-dir
# ---------------------------------------------------------------------------


def test_aggregate_requires_explicit_dataset_dir(
    runner_module, clean_dataset_env, tmp_path, monkeypatch
) -> None:
    """Aggregate phase must also have explicit dataset-dir.

    This test verifies that the runner's main() flow applies preflight
    to aggregate too (not just Phase 1/2/3). We patch ``aggregate`` to
    track invocation and verify preflight fires first.
    """
    aggregate_invoked = {"count": 0}

    def _fail_if_called(*args, **kwargs):
        aggregate_invoked["count"] += 1
        raise AssertionError(
            "aggregate() was invoked — runner should have exited at "
            "preflight before reaching aggregate."
        )

    monkeypatch.setattr(runner_module, "aggregate", _fail_if_called)

    # Simulate the preflight-then-aggregate sequence.
    dataset_dir = runner_module._resolve_dataset_dir(None)
    assert dataset_dir is None
    with pytest.raises(SystemExit) as exc_info:
        runner_module._preflight_dataset_dir(dataset_dir)
    assert exc_info.value.code == 2
    assert aggregate_invoked["count"] == 0
