from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def vocab_dataset_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "datasets" / "vocabulary-seed-v1"


@pytest.fixture
def vocab_cases(vocab_dataset_dir: Path) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for path in sorted((vocab_dataset_dir / "cases").glob("*.json")):
        cases.append(json.loads(path.read_text(encoding="utf-8")))
    return cases


def test_dataset_yaml_exists(vocab_dataset_dir: Path) -> None:
    assert (vocab_dataset_dir / "dataset.yaml").is_file()


def test_at_least_twelve_cases(vocab_cases: list[dict[str, object]]) -> None:
    assert len(vocab_cases) >= 12, (
        f"expected >=12 vocabulary cases, got {len(vocab_cases)}"
    )


def test_all_cases_have_required_top_level_fields(
    vocab_cases: list[dict[str, object]],
) -> None:
    required = {"schema_version", "id", "unit_id", "unit_text", "anchor_segments"}
    for case in vocab_cases:
        missing = required - case.keys()
        assert not missing, f"case={case.get('id')!r} missing keys={sorted(missing)}"


def test_all_cases_have_execution_block(
    vocab_cases: list[dict[str, object]],
) -> None:
    for case in vocab_cases:
        assert "execution" in case, f"case={case.get('id')!r} missing execution"
        execution = case["execution"]
        assert isinstance(execution, dict)
        assert "output" in execution
        assert "diagnostics" in execution


def test_case_ids_unique(vocab_cases: list[dict[str, object]]) -> None:
    ids = [c["id"] for c in vocab_cases]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"