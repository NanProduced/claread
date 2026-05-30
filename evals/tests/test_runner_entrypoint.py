from __future__ import annotations

from pathlib import Path

import pytest

from claread_eval.runner.config_loader import RunConfigLoadError, load_runner_config
from claread_eval.runner.entrypoint import run_from_config_file
from claread_eval.schemas.prompt_variant import PromptVariantLoadError


def test_load_runner_config_resolves_wrapper_fields(tmp_path: Path) -> None:
    variant_dir = tmp_path / "variant"
    variant_dir.mkdir()
    config_path = tmp_path / "run.yaml"
    config_path.write_text(
        "\n".join(
            [
                "run_id: config-test-001",
                "dataset_id: article-analysis-v1",
                "eval_purpose: prompt_experiment",
                "adapter_kind: fake",
                "runs_root: ./out-runs",
                "datasets_root: ./datasets",
                "fake_latency_seconds: 0.01",
                "prompt_variant_id: variant-a",
                "prompt_variant_path: ./variant",
                "model_selection: {}",
                "rag_mode: off",
                "trace_scope: off",
            ]
        ),
        encoding="utf-8",
    )

    config = load_runner_config(config_path)

    assert config.adapter_kind == "fake"
    assert config.fake_latency_seconds == 0.01
    assert config.run_config.eval_purpose == "prompt_experiment"
    assert config.run_config.prompt_variant_id == "variant-a"
    assert config.prompt_variant_path == variant_dir
    assert config.runs_root == tmp_path / "out-runs"
    assert config.datasets_root == tmp_path / "datasets"
    assert config.dataset_dir == tmp_path / "datasets" / "article-analysis-v1"
    assert config.run_dir == tmp_path / "out-runs" / "config-test-001"
    assert config.run_config.model_selection == {}


def test_load_runner_config_normalizes_nested_off_literals(tmp_path: Path) -> None:
    config_path = tmp_path / "run.yaml"
    config_path.write_text(
        "\n".join(
            [
                "run_id: config-test-002",
                "dataset_id: article-analysis-v1",
                "adapter_kind: fake",
                "model_selection:",
                "  nested:",
                "    rag_mode: off",
                "rag_mode: off",
                "trace_scope: off",
            ]
        ),
        encoding="utf-8",
    )

    config = load_runner_config(config_path)

    assert config.run_config.rag_mode == "off"
    assert config.run_config.trace_scope == "off"
    assert config.run_config.model_selection["nested"]["rag_mode"] == "off"


def test_load_runner_config_missing_file() -> None:
    with pytest.raises(RunConfigLoadError, match="not found"):
        load_runner_config("missing-run-config.yaml")


@pytest.mark.asyncio
async def test_run_from_config_file_writes_fake_run(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    datasets_root = repo_root / "evals" / "datasets"
    config_path = tmp_path / "smoke.yaml"
    variant_path = tmp_path / "variant.yaml"
    variant_path.write_text(
        "\n".join(
            [
                "variant_id: fake-variant",
                "target: article_analysis",
                "few_shot_mode: off",
            ]
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        "\n".join(
            [
                "run_id: entrypoint-smoke-001",
                "dataset_id: article-analysis-v1",
                "mode: workflow",
                "eval_purpose: dataset_regression",
                "adapter_kind: fake",
                "prompt_variant_id: fake-variant",
                f"prompt_variant_path: {variant_path.as_posix()}",
                f"runs_root: {tmp_path.as_posix()}",
                f"datasets_root: {datasets_root.as_posix()}",
                "fake_latency_seconds: 0.0",
                "model_selection: {}",
                "rag_mode: off",
                "trace_scope: off",
            ]
        ),
        encoding="utf-8",
    )

    report, run_dir = await run_from_config_file(config_path)

    assert report.run_id == "entrypoint-smoke-001"
    assert report.total_cases >= 3
    assert run_dir == tmp_path / "entrypoint-smoke-001"
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "report.md").is_file()
    artifact_path = run_dir / "cases" / "short-daily-intermediate.json"
    artifact = artifact_path.read_text(encoding="utf-8")
    assert "fake-variant" in artifact


@pytest.mark.asyncio
async def test_run_from_config_file_rejects_prompt_variant_mismatch(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    datasets_root = repo_root / "evals" / "datasets"
    variant_path = tmp_path / "variant.yaml"
    variant_path.write_text(
        "\n".join(
            [
                "variant_id: actual-variant",
                "target: article_analysis",
                "few_shot_mode: off",
            ]
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "smoke.yaml"
    config_path.write_text(
        "\n".join(
            [
                "run_id: entrypoint-smoke-variant-mismatch",
                "dataset_id: article-analysis-v1",
                "adapter_kind: fake",
                "prompt_variant_id: expected-variant",
                f"prompt_variant_path: {variant_path.as_posix()}",
                f"runs_root: {tmp_path.as_posix()}",
                f"datasets_root: {datasets_root.as_posix()}",
                "model_selection: {}",
                "rag_mode: off",
                "trace_scope: off",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(PromptVariantLoadError, match="mismatch"):
        await run_from_config_file(config_path)


@pytest.mark.asyncio
async def test_run_from_config_file_rejects_prompt_variant_with_rag(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    datasets_root = repo_root / "evals" / "datasets"
    variant_path = tmp_path / "variant.yaml"
    variant_path.write_text(
        "\n".join(
            [
                "variant_id: variant-a",
                "target: article_analysis",
                "few_shot_mode: off",
            ]
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "smoke.yaml"
    config_path.write_text(
        "\n".join(
            [
                "run_id: entrypoint-smoke-variant-rag",
                "dataset_id: article-analysis-v1",
                "adapter_kind: fake",
                "prompt_variant_id: variant-a",
                f"prompt_variant_path: {variant_path.as_posix()}",
                f"runs_root: {tmp_path.as_posix()}",
                f"datasets_root: {datasets_root.as_posix()}",
                "model_selection: {}",
                "rag_mode: settings",
                "trace_scope: off",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="rag_mode='off'"):
        await run_from_config_file(config_path)
