from __future__ import annotations

from pathlib import Path

import orjson
import yaml
from pydantic import BaseModel

from claread_eval.adapter.factory import create_adapter_client
from claread_eval.runner.adapter_config import adapter_run_config
from claread_eval.schemas.dataset import AdHocEvalCaseInput, EvalCase, EvalDataset
from claread_eval.schemas.report import EvalReport

from .config_loader import RunnerFileConfig, load_runner_config
from .simple_runner import run_eval


class ManualCaseLoadError(ValueError):
    pass


class ManualEvalRunResult(BaseModel):
    report: EvalReport
    run_dir: Path
    case: EvalCase


def load_ad_hoc_case_input(path: str | Path) -> AdHocEvalCaseInput:
    case_path = Path(path).resolve()
    if not case_path.is_file():
        raise ManualCaseLoadError(f"Manual case file not found: {case_path}")

    if case_path.suffix.lower() == ".json":
        raw = orjson.loads(case_path.read_bytes())
    else:
        raw = yaml.safe_load(case_path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise ManualCaseLoadError("Manual case file must contain an object")
    return AdHocEvalCaseInput.model_validate(raw)


async def run_manual_case(
    *,
    config: RunnerFileConfig,
    ad_hoc_input: AdHocEvalCaseInput,
    case_id: str | None = None,
) -> ManualEvalRunResult:
    case = ad_hoc_input.to_eval_case(case_id=case_id)
    dataset = EvalDataset(
        id=config.run_config.dataset_id,
        target="article_analysis",
        description="Manual ad-hoc eval run",
        tags=["manual", "adhoc"],
    )
    adapter = create_adapter_client(
        config.adapter_kind,
        fake_latency_seconds=config.fake_latency_seconds,
    )
    runtime_adapter_config = adapter_run_config(config)
    report = await run_eval(
        dataset=dataset,
        cases=[case],
        run_config=config.run_config,
        adapter=adapter,
        runs_root=config.runs_root,
        adapter_run_config=runtime_adapter_config,
    )
    return ManualEvalRunResult(report=report, run_dir=config.run_dir, case=case)


async def run_manual_case_from_files(
    *,
    config_path: str | Path,
    case_path: str | Path,
    case_id: str | None = None,
) -> ManualEvalRunResult:
    config = load_runner_config(config_path)
    ad_hoc_input = load_ad_hoc_case_input(case_path)
    return await run_manual_case(config=config, ad_hoc_input=ad_hoc_input, case_id=case_id)
