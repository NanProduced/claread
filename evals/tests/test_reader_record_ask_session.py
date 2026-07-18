"""Tests for RunSessionLayout — single source of truth for run paths.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirement: RunSessionLayout 深模块（P0-1）.

Covers:
- Path resolver correctness (run_dir / artifact_dir / prior_artifact_dir).
- Artifact filename determinism + no-collision across (case, model, thinking,
  run_index).
- Path-traversal fail-closed for run_id / prior_run_id / case_id / model.
- ``from_env`` reads CLAREAD_R4_A3_RUN_ID / CLAREAD_R4_A3_PRIOR_RUN_ID and
  rejects missing run id (no "guess latest run" allowed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claread_eval.reader_record_ask.session import (
    ENV_PRIOR_RUN_ID,
    ENV_RUN_ID,
    RunSessionLayout,
    RunSessionLayoutError,
)

# ---------------------------------------------------------------------------
# Construction & path resolution
# ---------------------------------------------------------------------------


def test_run_dir_and_artifact_dir_use_run_id_subdirectory(tmp_path: Path) -> None:
    layout = RunSessionLayout(
        runs_root=tmp_path, run_id="phase1-abc", prior_run_id=None
    )
    assert layout.runs_root == tmp_path
    assert layout.run_id == "phase1-abc"
    assert layout.prior_run_id is None
    assert layout.run_dir == tmp_path / "phase1-abc"
    assert layout.artifact_dir == tmp_path / "phase1-abc" / "artifacts"


def test_prior_artifact_dir_resolves_when_prior_run_id_set(tmp_path: Path) -> None:
    layout = RunSessionLayout(
        runs_root=tmp_path,
        run_id="phase2-xyz",
        prior_run_id="phase1-abc",
    )
    assert layout.prior_run_id == "phase1-abc"
    assert layout.prior_artifact_dir == tmp_path / "phase1-abc" / "artifacts"
    # The current run_dir is distinct from the prior.
    assert layout.run_dir == tmp_path / "phase2-xyz"


def test_prior_artifact_dir_is_none_when_prior_run_id_omitted(tmp_path: Path) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-abc")
    assert layout.prior_artifact_dir is None


# ---------------------------------------------------------------------------
# Artifact filename: determinism + no collision
# ---------------------------------------------------------------------------


def test_artifact_path_includes_all_four_dimensions(tmp_path: Path) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-abc")
    p = layout.artifact_path(
        case_id="bbc_city_enum",
        model_short_name="deepseek-v4",
        thinking_enabled=False,
        run_index=0,
    )
    assert p == (
        tmp_path
        / "phase1-abc"
        / "artifacts"
        / "bbc_city_enum__deepseek-v4__nothinking__000.json"
    )


def test_artifact_path_does_not_collide_across_repetitions(tmp_path: Path) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-abc")
    paths = [
        layout.artifact_path(
            case_id="bbc_city_enum",
            model_short_name="deepseek-v4",
            thinking_enabled=False,
            run_index=i,
        )
        for i in range(3)
    ]
    assert len({str(p) for p in paths}) == 3
    # 3-digit zero-padded run_index.
    assert paths[0].name.endswith("__000.json")
    assert paths[1].name.endswith("__001.json")
    assert paths[2].name.endswith("__002.json")


def test_artifact_path_does_not_collide_across_thinking_configs(
    tmp_path: Path,
) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-abc")
    no_thinking = layout.artifact_path(
        case_id="bbc_main_idea",
        model_short_name="deepseek-v4",
        thinking_enabled=False,
        run_index=0,
    )
    with_thinking = layout.artifact_path(
        case_id="bbc_main_idea",
        model_short_name="deepseek-v4",
        thinking_enabled=True,
        run_index=0,
    )
    assert no_thinking != with_thinking
    assert "nothinking" in no_thinking.name
    assert "thinking" in with_thinking.name


def test_artifact_path_model_none_uses_none_token(tmp_path: Path) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-abc")
    p = layout.artifact_path(
        case_id="bbc_main_idea",
        model_short_name=None,
        thinking_enabled=False,
        run_index=0,
    )
    assert "__none__nothinking__000.json" in p.name


# ---------------------------------------------------------------------------
# Path traversal fail-closed (P0-1 core requirement)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_run_id",
    [
        "..",
        "../",
        "..\\",
        "foo/../bar",
        "foo/bar",
        "foo\\bar",
        "foo bar",  # space
        "foo.bar",  # dot not allowed for run_id
        "测试",  # unicode
        "",
    ],
)
def test_run_id_rejects_unsafe_chars(tmp_path: Path, bad_run_id: str) -> None:
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout(runs_root=tmp_path, run_id=bad_run_id)


@pytest.mark.parametrize(
    "bad_prior_run_id",
    ["..", "../escape", "foo/bar", "foo\\bar", "foo bar", "测试", ""],
)
def test_prior_run_id_rejects_unsafe_chars(
    tmp_path: Path, bad_prior_run_id: str
) -> None:
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout(
            runs_root=tmp_path,
            run_id="phase1-ok",
            prior_run_id=bad_prior_run_id,
        )


def test_run_id_rejects_non_string_type(tmp_path: Path) -> None:
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout(runs_root=tmp_path, run_id=123)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_case_id",
    ["", "foo/bar", "foo\\bar", "foo bar", "测试"],
)
def test_artifact_path_rejects_unsafe_case_id(
    tmp_path: Path, bad_case_id: str
) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-ok")
    with pytest.raises(RunSessionLayoutError):
        layout.artifact_path(
            case_id=bad_case_id,
            model_short_name="deepseek-v4",
            thinking_enabled=False,
            run_index=0,
        )


@pytest.mark.parametrize(
    "bad_model",
    ["foo/bar", "foo\\bar", "foo bar", "测试"],
)
def test_artifact_path_rejects_unsafe_model_name(
    tmp_path: Path, bad_model: str
) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-ok")
    with pytest.raises(RunSessionLayoutError):
        layout.artifact_path(
            case_id="bbc_main_idea",
            model_short_name=bad_model,
            thinking_enabled=False,
            run_index=0,
        )


def test_artifact_path_rejects_negative_run_index(tmp_path: Path) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-ok")
    with pytest.raises(RunSessionLayoutError):
        layout.artifact_path(
            case_id="bbc_main_idea",
            model_short_name="deepseek-v4",
            thinking_enabled=False,
            run_index=-1,
        )


def test_artifact_path_rejects_run_index_above_999(tmp_path: Path) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-ok")
    with pytest.raises(RunSessionLayoutError):
        layout.artifact_path(
            case_id="bbc_main_idea",
            model_short_name="deepseek-v4",
            thinking_enabled=False,
            run_index=1000,
        )


# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------


def test_from_env_reads_run_id_and_prior_run_id(tmp_path: Path) -> None:
    env = {
        ENV_RUN_ID: "phase1-from-env",
        ENV_PRIOR_RUN_ID: "phase0-prior",
    }
    layout = RunSessionLayout.from_env(runs_root=tmp_path, env=env)
    assert layout.run_id == "phase1-from-env"
    assert layout.prior_run_id == "phase0-prior"
    assert layout.prior_artifact_dir == (
        tmp_path / "phase0-prior" / "artifacts"
    )


def test_from_env_allows_missing_prior_run_id_for_phase1(tmp_path: Path) -> None:
    env = {ENV_RUN_ID: "phase1-only"}
    layout = RunSessionLayout.from_env(runs_root=tmp_path, env=env)
    assert layout.run_id == "phase1-only"
    assert layout.prior_run_id is None
    assert layout.prior_artifact_dir is None


def test_from_env_strips_whitespace_from_run_id(tmp_path: Path) -> None:
    env = {ENV_RUN_ID: "  phase1-stripped  "}
    layout = RunSessionLayout.from_env(runs_root=tmp_path, env=env)
    assert layout.run_id == "phase1-stripped"


def test_from_env_strips_whitespace_from_prior_run_id(tmp_path: Path) -> None:
    env = {
        ENV_RUN_ID: "phase2-ok",
        ENV_PRIOR_RUN_ID: "  phase1-ok  ",
    }
    layout = RunSessionLayout.from_env(runs_root=tmp_path, env=env)
    assert layout.prior_run_id == "phase1-ok"


def test_from_env_rejects_missing_run_id(tmp_path: Path) -> None:
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout.from_env(runs_root=tmp_path, env={})


def test_from_env_rejects_empty_run_id(tmp_path: Path) -> None:
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout.from_env(
            runs_root=tmp_path, env={ENV_RUN_ID: "   "}
        )


def test_from_env_rejects_unsafe_run_id(tmp_path: Path) -> None:
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout.from_env(
            runs_root=tmp_path, env={ENV_RUN_ID: "../escape"}
        )


def test_from_env_rejects_unsafe_prior_run_id(tmp_path: Path) -> None:
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout.from_env(
            runs_root=tmp_path,
            env={
                ENV_RUN_ID: "phase2-ok",
                ENV_PRIOR_RUN_ID: "../escape",
            },
        )


# ---------------------------------------------------------------------------
# Round-trip: writer + reader share the same resolver
# ---------------------------------------------------------------------------


def test_writer_and_reader_resolve_to_same_path(tmp_path: Path) -> None:
    """Harness (writer) and aggregate (reader) must use the same path.

    This is the core P0-1 contract: a single resolver shared by write and
    read. If the harness writes via ``RunSessionLayout(run_id=X)`` and the
    aggregate reads via ``RunSessionLayout(run_id=X)``, both must resolve
    to the same artifact path for the same (case, model, thinking, run_index).
    """
    writer = RunSessionLayout(runs_root=tmp_path, run_id="phase1-shared")
    reader = RunSessionLayout(runs_root=tmp_path, run_id="phase1-shared")

    write_path = writer.artifact_path(
        case_id="bbc_main_idea",
        model_short_name="deepseek-v4",
        thinking_enabled=False,
        run_index=2,
    )
    read_path = reader.artifact_path(
        case_id="bbc_main_idea",
        model_short_name="deepseek-v4",
        thinking_enabled=False,
        run_index=2,
    )
    assert write_path == read_path


def test_phase2_uses_prior_artifact_dir_not_root_scan(tmp_path: Path) -> None:
    """Phase 2 must read prior artifacts via prior_artifact_dir, not by
    scanning ``runs_root`` for the "latest" run.

    This is the regression test for the prior harness behavior of guessing
    the latest run by scanning the root directory.
    """
    # Simulate a runs_root with multiple phase directories.
    (tmp_path / "phase1-aaa" / "artifacts").mkdir(parents=True)
    (tmp_path / "phase1-bbb" / "artifacts").mkdir(parents=True)
    (tmp_path / "phase1-ccc" / "artifacts").mkdir(parents=True)

    # Phase 2 explicitly references phase1-bbb (NOT the lexicographically
    # latest, NOT the mtime latest — explicit prior_run_id only).
    layout = RunSessionLayout(
        runs_root=tmp_path,
        run_id="phase2-xxx",
        prior_run_id="phase1-bbb",
    )
    assert layout.prior_artifact_dir == tmp_path / "phase1-bbb" / "artifacts"


# ---------------------------------------------------------------------------
# validate() explicit call
# ---------------------------------------------------------------------------


def test_validate_does_not_create_directories(tmp_path: Path) -> None:
    """Layout must be pure — no IO side effects."""
    layout = RunSessionLayout(runs_root=tmp_path, run_id="phase1-pure")
    layout.validate()
    # Nothing should have been created.
    assert not (tmp_path / "phase1-pure").exists()
    assert not (tmp_path / "phase1-pure" / "artifacts").exists()


def test_construction_calls_validate_implicitly(tmp_path: Path) -> None:
    """Bad run_id must be rejected at construction time, not on first use."""
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout(runs_root=tmp_path, run_id="../escape")


# ---------------------------------------------------------------------------
# Allowed characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good_run_id",
    [
        "phase1-abc",
        "phase2-XYZ",
        "phase3_2026",
        "phase1-abc_def-2026",
        "P1",
        "a",
        "1",
        "-",
        "_",
        "abc123-DEF_456",
    ],
)
def test_run_id_accepts_safe_chars(tmp_path: Path, good_run_id: str) -> None:
    layout = RunSessionLayout(runs_root=tmp_path, run_id=good_run_id)
    assert layout.run_id == good_run_id
