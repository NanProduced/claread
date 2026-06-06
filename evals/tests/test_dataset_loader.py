import json
from pathlib import Path

import pytest
import yaml

from claread_eval.loader.dataset_loader import DatasetLoadError, load_dataset
from claread_eval.schemas.dataset import EvalDataset


@pytest.fixture
def sample_dataset_dir(tmp_path: Path) -> Path:
    dataset_dir = tmp_path / "test-dataset"
    dataset_dir.mkdir()
    cases_dir = dataset_dir / "cases"
    cases_dir.mkdir()

    dataset_yaml = {
        "id": "test-dataset",
        "schema_version": "eval-dataset-v1",
        "target": "article_analysis",
        "description": "Test dataset",
        "case_globs": ["cases/*.json"],
        "tags": ["test"],
    }
    (dataset_dir / "dataset.yaml").write_text(
        yaml.dump(dataset_yaml), encoding="utf-8"
    )

    case_data = {
        "id": "case-001",
        "text": "Hello world. This is a test.",
        "reading_goal": "daily_reading",
        "reading_variant": "intermediate_reading",
        "source_type": "user_input",
        "tags": ["test"],
        "expected": {"min_translation_coverage": 0.9},
    }
    (cases_dir / "case-001.json").write_text(
        json.dumps(case_data), encoding="utf-8"
    )

    return dataset_dir


def test_load_dataset_success(sample_dataset_dir: Path) -> None:
    dataset, cases = load_dataset(sample_dataset_dir)
    assert isinstance(dataset, EvalDataset)
    assert dataset.id == "test-dataset"
    assert len(cases) == 1
    assert cases[0].id == "case-001"
    assert cases[0].reading_goal == "daily_reading"


def test_load_dataset_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(DatasetLoadError, match="not found"):
        load_dataset(tmp_path / "nonexistent")


def test_load_dataset_missing_yaml(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(DatasetLoadError, match="dataset.yaml not found"):
        load_dataset(empty_dir)


def test_load_dataset_invalid_variant(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "bad-variant"
    dataset_dir.mkdir()
    cases_dir = dataset_dir / "cases"
    cases_dir.mkdir()

    dataset_yaml = {
        "id": "bad-variant",
        "target": "article_analysis",
        "case_globs": ["cases/*.json"],
    }
    (dataset_dir / "dataset.yaml").write_text(
        yaml.dump(dataset_yaml), encoding="utf-8"
    )

    case_data = {
        "id": "case-bad",
        "text": "Some text.",
        "reading_goal": "daily_reading",
        "reading_variant": "academic_general",
    }
    (cases_dir / "case-bad.json").write_text(
        json.dumps(case_data), encoding="utf-8"
    )

    with pytest.raises(ValueError):
        load_dataset(dataset_dir)


def test_load_dataset_duplicate_ids(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dup-ids"
    dataset_dir.mkdir()
    cases_dir = dataset_dir / "cases"
    cases_dir.mkdir()

    dataset_yaml = {
        "id": "dup-ids",
        "target": "article_analysis",
        "case_globs": ["cases/*.json"],
    }
    (dataset_dir / "dataset.yaml").write_text(
        yaml.dump(dataset_yaml), encoding="utf-8"
    )

    case_data = {
        "id": "case-dup",
        "text": "Text one.",
        "reading_goal": "daily_reading",
        "reading_variant": "intermediate_reading",
    }
    (cases_dir / "case-dup.json").write_text(
        json.dumps(case_data), encoding="utf-8"
    )
    (cases_dir / "case-dup-2.json").write_text(
        json.dumps(case_data), encoding="utf-8"
    )

    with pytest.raises(DatasetLoadError, match="Duplicate case id"):
        load_dataset(dataset_dir)


def test_load_real_dataset() -> None:
    real_path = Path(__file__).parent.parent / "datasets" / "article-analysis-v1"
    if not real_path.is_dir():
        pytest.skip("Real dataset not available")
    dataset, cases = load_dataset(real_path)
    assert dataset.id == "article-analysis-v1"
    assert len(cases) >= 3
    case_ids = {c.id for c in cases}
    assert "short-daily-intermediate" in case_ids
    assert "long-daily-intensive" in case_ids
    assert "academic-extended" in case_ids
