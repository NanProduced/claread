from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from claread_eval.adapter.factory import create_adapter_client
from claread_eval.loader.dataset_loader import load_dataset
from claread_eval.runner.adapter_config import adapter_run_config
from claread_eval.runner.config_loader import RunnerFileConfig, load_runner_config
from claread_eval.runner.simple_runner import run_eval
from claread_eval.schemas.report import EvalReport


async def run_from_config(config: RunnerFileConfig) -> tuple[EvalReport, Path]:
    dataset, cases = load_dataset(config.dataset_dir)
    runtime_adapter_config = adapter_run_config(config)
    adapter = create_adapter_client(
        config.adapter_kind,
        fake_latency_seconds=config.fake_latency_seconds,
    )
    report = await run_eval(
        dataset=dataset,
        cases=cases,
        run_config=config.run_config,
        adapter=adapter,
        runs_root=config.runs_root,
        adapter_run_config=runtime_adapter_config,
    )
    return report, config.run_dir


async def run_from_config_file(path: str | Path) -> tuple[EvalReport, Path]:
    return await run_from_config(load_runner_config(path))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Claread file-based eval.")
    parser.add_argument("--config", required=True, help="Path to run_config.yaml")
    args = parser.parse_args(argv)

    report, run_dir = asyncio.run(run_from_config_file(args.config))
    print(f"Eval run complete: {report.run_id}")
    print(f"Run directory: {run_dir}")
    print(f"Passed/Failed/Errored: {report.passed}/{report.failed}/{report.errored}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
