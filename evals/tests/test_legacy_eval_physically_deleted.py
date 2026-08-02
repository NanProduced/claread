"""Physical cutover guard: legacy eval substrates must stay deleted.

Asserts that the Workflow Lab / Node Lab / Eval Center packages and shared
legacy modules removed in the CUTOVER-CONTROL-EVAL physical phase can no
longer be imported and no longer exist on disk, so an accidental revival is
caught as a test failure. Also pins that the protected substrates (Reader
Record Ask + Vocabulary eval) are still present.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

PKG_ROOT = pathlib.Path(__file__).resolve().parents[1] / "claread_eval"

DELETED_PACKAGES = [
    "claread_eval.adapter",
    "claread_eval.judge",
    "claread_eval.judge_bridge",
    "claread_eval.node_lab_judge",
    "claread_eval.reports",
    "claread_eval.runner_bridge",
    "claread_eval.writer",
]

DELETED_MODULES = [
    "claread_eval.graders.base",
    "claread_eval.graders.schema_presence",
    "claread_eval.graders.status_error",
    "claread_eval.graders.translation_coverage",
    "claread_eval.graders.warning_drop_summary",
    "claread_eval.loader.dataset_loader",
    "claread_eval.runner.adapter_config",
    "claread_eval.runner.config_loader",
    "claread_eval.runner.entrypoint",
    "claread_eval.runner.manual_case",
    "claread_eval.runner.simple_runner",
    "claread_eval.schemas.dataset",
    "claread_eval.schemas.grader",
    "claread_eval.schemas.judge",
    "claread_eval.schemas.prompt_variant",
    "claread_eval.schemas.report",
    "claread_eval.schemas.rubric",
    "claread_eval.schemas.run",
    "claread_eval.security",
]


@pytest.mark.parametrize("mod", DELETED_PACKAGES + DELETED_MODULES)
def test_legacy_module_not_importable(mod: str) -> None:
    assert importlib.util.find_spec(mod) is None, (
        f"{mod} must stay physically deleted (importable again)"
    )


def test_legacy_package_dirs_absent() -> None:
    for sub in (
        "adapter",
        "judge",
        "judge_bridge",
        "node_lab_judge",
        "reports",
        "runner_bridge",
        "writer",
    ):
        assert not (PKG_ROOT / sub).exists(), f"claread_eval/{sub}/ must stay deleted"


def test_kept_substrates_still_present() -> None:
    for mod in (
        "claread_eval.reader_record_ask",
        "claread_eval.graders.vocabulary",
        "claread_eval.loader.vocabulary_dataset_loader",
        "claread_eval.runner.vocabulary_runner",
        "claread_eval.schemas.vocabulary",
    ):
        assert importlib.util.find_spec(mod) is not None, f"{mod} must stay present"
