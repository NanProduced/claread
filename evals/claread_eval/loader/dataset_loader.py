from __future__ import annotations

import json
from pathlib import Path

import yaml

from claread_eval.schemas.dataset import EvalCase, EvalDataset


class DatasetLoadError(Exception):
    pass


def load_dataset(dataset_dir: str | Path) -> tuple[EvalDataset, list[EvalCase]]:
    dataset_dir = Path(dataset_dir)
    if not dataset_dir.is_dir():
        raise DatasetLoadError(f"Dataset directory not found: {dataset_dir}")

    yaml_path = dataset_dir / "dataset.yaml"
    if not yaml_path.is_file():
        raise DatasetLoadError(f"dataset.yaml not found in {dataset_dir}")

    with yaml_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    dataset = EvalDataset.model_validate(raw)

    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for glob_pattern in dataset.case_globs:
        for case_path in sorted(dataset_dir.glob(glob_pattern)):
            with case_path.open("r", encoding="utf-8") as f:
                case_raw = json.load(f)
            case = EvalCase.model_validate(case_raw)
            if case.id in seen_ids:
                raise DatasetLoadError(f"Duplicate case id: {case.id} (from {case_path})")
            seen_ids.add(case.id)
            cases.append(case)

    return dataset, cases
