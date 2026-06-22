from __future__ import annotations

import json
from pathlib import Path

import yaml

from claread_eval.schemas.vocabulary import VocabularyEvalCase, VocabularyEvalDataset


class VocabularyDatasetLoadError(Exception):
    """Raised when a vocabulary seed dataset directory cannot be loaded."""


def load_vocabulary_dataset(
    dataset_dir: str | Path,
) -> tuple[VocabularyEvalDataset, list[VocabularyEvalCase]]:
    """Load a vocabulary eval dataset.

    Expected layout::

        <dataset_dir>/dataset.yaml
        <dataset_dir>/cases/*.json (one VocabularyEvalCase per file)
    """
    dataset_dir = Path(dataset_dir)
    if not dataset_dir.is_dir():
        raise VocabularyDatasetLoadError(
            f"Vocabulary dataset directory not found: {dataset_dir}"
        )

    yaml_path = dataset_dir / "dataset.yaml"
    if not yaml_path.is_file():
        raise VocabularyDatasetLoadError(
            f"dataset.yaml not found in {dataset_dir}"
        )

    with yaml_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise VocabularyDatasetLoadError(
            f"dataset.yaml must be a mapping, got {type(raw).__name__}"
        )

    dataset = VocabularyEvalDataset.model_validate(raw)

    cases: list[VocabularyEvalCase] = []
    seen_ids: set[str] = set()
    for glob_pattern in dataset.case_globs:
        for case_path in sorted(dataset_dir.glob(glob_pattern)):
            with case_path.open("r", encoding="utf-8") as f:
                case_raw = json.load(f)
            case = VocabularyEvalCase.model_validate(case_raw)
            if case.id in seen_ids:
                raise VocabularyDatasetLoadError(
                    f"Duplicate vocabulary case id: {case.id} (from {case_path})"
                )
            seen_ids.add(case.id)
            cases.append(case)

    return dataset, cases