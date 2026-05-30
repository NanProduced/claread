from __future__ import annotations

from pathlib import Path

import pytest

from claread_eval.runner.config_loader import load_runner_config
from claread_eval.runner.manual_case import (
    ManualCaseLoadError,
    load_ad_hoc_case_input,
    run_manual_case_from_files,
)
from claread_eval.schemas.dataset import AdHocEvalCaseInput


def test_ad_hoc_eval_case_input_to_eval_case_has_stable_default_id() -> None:
    ad_hoc = AdHocEvalCaseInput(
        text="This is a manually pasted article.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )

    first = ad_hoc.to_eval_case()
    second = ad_hoc.to_eval_case()

    assert first.id == second.id
    assert first.id.startswith("adhoc-")
    assert first.origin == "adhoc"
    assert first.tags == ["adhoc"]
    assert first.text == "This is a manually pasted article."


def test_ad_hoc_eval_case_input_rejects_invalid_variant() -> None:
    with pytest.raises(ValueError):
        AdHocEvalCaseInput(
            text="Manual article.",
            reading_goal="daily_reading",
            reading_variant="academic_general",
        )


def test_ad_hoc_eval_case_input_accepts_explicit_case_id() -> None:
    case = AdHocEvalCaseInput(text="Manual article.").to_eval_case(case_id="manual-1")
    assert case.id == "manual-1"


def test_load_ad_hoc_case_input_from_yaml(tmp_path: Path) -> None:
    case_path = tmp_path / "manual.yaml"
    case_path.write_text(
        "\n".join(
            [
                "text: Manual article.",
                "reading_goal: daily_reading",
                "reading_variant: intermediate_reading",
                "tags:",
                "  - adhoc",
                "  - prompt-debug",
            ]
        ),
        encoding="utf-8",
    )

    case_input = load_ad_hoc_case_input(case_path)

    assert case_input.text == "Manual article."
    assert case_input.tags == ["adhoc", "prompt-debug"]


def test_load_ad_hoc_case_input_rejects_missing_file() -> None:
    with pytest.raises(ManualCaseLoadError, match="not found"):
        load_ad_hoc_case_input("missing-manual-case.yaml")


@pytest.mark.asyncio
async def test_run_manual_case_from_files_writes_only_run_artifacts(tmp_path: Path) -> None:
    config_path = tmp_path / "run.yaml"
    config_path.write_text(
        "\n".join(
            [
                "run_id: manual-run-001",
                "dataset_id: manual-adhoc",
                "eval_purpose: manual_debug",
                "adapter_kind: fake",
                f"runs_root: {tmp_path.as_posix()}",
                "model_selection: {}",
                "rag_mode: off",
                "trace_scope: off",
            ]
        ),
        encoding="utf-8",
    )
    case_path = tmp_path / "manual.yaml"
    case_path.write_text(
        "\n".join(
            [
                "text: Manual article. It has two sentences.",
                "reading_goal: daily_reading",
                "reading_variant: intermediate_reading",
            ]
        ),
        encoding="utf-8",
    )

    result = await run_manual_case_from_files(
        config_path=config_path,
        case_path=case_path,
        case_id="manual-case-001",
    )

    assert result.case.id == "manual-case-001"
    assert result.case.origin == "adhoc"
    assert result.report.total_cases == 1
    assert (tmp_path / "manual-run-001" / "cases" / "manual-case-001.json").is_file()
    assert not (tmp_path / "manual-adhoc" / "cases" / "manual-case-001.json").exists()


def test_load_runner_config_for_manual_case_defaults_dataset_root(tmp_path: Path) -> None:
    config_path = tmp_path / "run.yaml"
    config_path.write_text(
        "\n".join(
            [
                "run_id: manual-run-002",
                "dataset_id: manual-adhoc",
                "eval_purpose: manual_debug",
                "adapter_kind: fake",
                "model_selection: {}",
                "rag_mode: off",
                "trace_scope: off",
            ]
        ),
        encoding="utf-8",
    )

    config = load_runner_config(config_path)

    assert config.run_config.eval_purpose == "manual_debug"
