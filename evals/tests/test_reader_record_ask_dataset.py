"""Dataset loader/schema tests for R4-A3 reader-record-ask.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirement (P0 dataset Git governance): unit tests MUST NOT depend on
the local ignored working dataset under ``evals/tmp/``. Instead, each
test builds a minimal synthetic dataset via factory + ``tmp_path`` and
exercises the loader/schema against it. No real Reading Record UUID,
BBC body, or run artifact may appear in tracked fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from claread_eval.reader_record_ask.loader import (
    ReaderRecordAskDatasetLoadError,
    load_r4_a3_dataset,
    serialize_dataset,
    validate_round_trip,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Dataset,
    ReaderRecordAskR4A3Expected,
)

# ---------------------------------------------------------------------------
# Factory helpers — build synthetic cases/datasets without touching the
# ignored local working dataset under ``evals/tmp/``.
# ---------------------------------------------------------------------------


def _make_case(
    *,
    case_id: str,
    source_kind: str = "synthetic_short",
    question_category: str = "main_idea",
    article_text: str | None = "synthetic article body for testing.",
    article_title: str | None = "synthetic title",
    record_id: str | None = None,
    expected: ReaderRecordAskR4A3Expected | None = None,
    phase_tags: list[str] | None = None,
) -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind=source_kind,  # type: ignore[arg-type]
        record_id=record_id,
        article_text=article_text,
        article_title=article_title,
        input_mode="manual",
        selection=None,
        rag_mode="off",
        source_metadata="known_synthetic" if source_kind.startswith("synthetic") else "known_bbc",
        baseline_mode="complete",
        question="测试问题？",
        question_category=question_category,  # type: ignore[arg-type]
        expected=expected or ReaderRecordAskR4A3Expected(),
        phase_tags=phase_tags or [],
    )


def _write_dataset(
    tmp_path: Path,
    cases: list[ReaderRecordAskR4A3Case],
    *,
    dataset_id: str = "reader-record-ask-r4-a3",
    case_globs: list[str] | None = None,
) -> Path:
    """Write a minimal synthetic dataset to ``tmp_path`` and return it.

    Layout matches what :func:`load_r4_a3_dataset` expects:
    ``<dir>/dataset.yaml`` + ``<dir>/cases/<id>.json``.
    """
    dataset_dir = tmp_path / "dataset"
    cases_dir = dataset_dir / "cases"
    cases_dir.mkdir(parents=True)

    manifest: dict[str, object] = {
        "id": dataset_id,
        "schema_version": "r4-a3-dataset-v1",
        "description": "synthetic test dataset",
        "case_globs": case_globs or ["cases/*.json"],
        "tags": [],
    }
    (dataset_dir / "dataset.yaml").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    for case in cases:
        (cases_dir / f"{case.id}.json").write_text(
            case.model_dump_json(indent=2), encoding="utf-8"
        )

    return dataset_dir


# ---------------------------------------------------------------------------
# Loader tests — use factory + tmp_path
# ---------------------------------------------------------------------------


def test_dataset_loads_from_disk(tmp_path: Path) -> None:
    cases = [
        _make_case(case_id=f"case-{i}", phase_tags=["real_phase1"])
        for i in range(3)
    ]
    dataset_dir = _write_dataset(tmp_path, cases)
    dataset = load_r4_a3_dataset(dataset_dir)

    assert dataset.id == "reader-record-ask-r4-a3"
    assert dataset.schema_version == "r4-a3-dataset-v1"
    assert len(dataset.cases) == 3


def test_case_ids_unique(tmp_path: Path) -> None:
    cases = [
        _make_case(case_id="alpha"),
        _make_case(case_id="beta"),
        _make_case(case_id="gamma"),
    ]
    dataset_dir = _write_dataset(tmp_path, cases)
    dataset = load_r4_a3_dataset(dataset_dir)

    ids = [c.id for c in dataset.cases]
    assert len(ids) == len(set(ids)), f"duplicate case ids: {ids}"


def test_duplicate_case_ids_raise(tmp_path: Path) -> None:
    """Two case files with the same id must fail closed at load time."""
    cases = [
        _make_case(case_id="dup"),
        _make_case(case_id="dup"),
    ]
    # Write the second case to a separate file with a different filename
    # but the same id — the loader reads id from JSON content, not filename.
    dataset_dir = _write_dataset(tmp_path, cases[:1])
    cases_dir = dataset_dir / "cases"
    (cases_dir / "dup-again.json").write_text(
        cases[1].model_dump_json(indent=2), encoding="utf-8"
    )

    with pytest.raises(ReaderRecordAskDatasetLoadError) as exc_info:
        load_r4_a3_dataset(dataset_dir)
    assert "Duplicate R4-A3 case id" in str(exc_info.value)


def test_round_trip_serialization_stable(tmp_path: Path) -> None:
    cases = [
        _make_case(
            case_id="round-trip",
            expected=ReaderRecordAskR4A3Expected(
                expected_entity_set={"city": ["CityA", "CityB"]},
                allowed_temporal_claims=["2026"],
                allowed_numerics=["42"],
                requested_count=2,
                requested_count_kind="exercise_items",
                must_declare_no_year=False,
            ),
        )
    ]
    dataset_dir = _write_dataset(tmp_path, cases)
    dataset = load_r4_a3_dataset(dataset_dir)

    assert validate_round_trip(dataset) is True
    serialized = serialize_dataset(dataset)
    rebuilt = ReaderRecordAskR4A3Dataset.model_validate_json(serialized)
    assert rebuilt.model_dump() == dataset.model_dump()


def test_bbc_cases_have_no_article_text(tmp_path: Path) -> None:
    """BBC-sourced cases must leave article_text/article_title null — the
    harness loads body content from the Reading Record at runtime.
    """
    cases = [
        _make_case(
            case_id="bbc-1",
            source_kind="bbc_record",
            article_text=None,
            article_title=None,
            record_id="synthetic-record-id",
        ),
        _make_case(
            case_id="synthetic-1",
            source_kind="synthetic_short",
            article_text="synthetic body",
            article_title="synthetic title",
        ),
    ]
    dataset_dir = _write_dataset(tmp_path, cases)
    dataset = load_r4_a3_dataset(dataset_dir)

    bbc_cases = [c for c in dataset.cases if c.source_kind == "bbc_record"]
    assert bbc_cases, "expected at least one bbc_record case"
    for case in bbc_cases:
        assert case.article_text is None, (
            f"bbc case {case.id} must have null article_text"
        )
        assert case.article_title is None, (
            f"bbc case {case.id} must have null article_title"
        )


def test_exercise_cases_have_requested_count(tmp_path: Path) -> None:
    exercise_categories = {"exercise_one", "multiple_choice_one"}
    cases = [
        _make_case(
            case_id="ex-1",
            question_category="exercise_one",
            expected=ReaderRecordAskR4A3Expected(
                requested_count=1,
                requested_count_kind="exercise_items",
            ),
        ),
        _make_case(
            case_id="mc-1",
            question_category="multiple_choice_one",
            expected=ReaderRecordAskR4A3Expected(
                requested_count=1,
                requested_count_kind="exercise_items",
            ),
        ),
    ]
    dataset_dir = _write_dataset(tmp_path, cases)
    dataset = load_r4_a3_dataset(dataset_dir)

    exercise_cases = [
        c
        for c in dataset.cases
        if c.question_category in exercise_categories
    ]
    assert exercise_cases, "expected at least one exercise-style case"
    for case in exercise_cases:
        assert case.expected.requested_count == 1, (
            f"exercise case {case.id} requested_count="
            f"{case.expected.requested_count} (expected 1)"
        )


def test_no_real_content_in_synthetic_cases(tmp_path: Path) -> None:
    """Synthetic cases must not carry real-sourced body content.

    This is a generic guard: synthetic cases' article_text must not
    contain placeholder tokens that would indicate real article content
    was copied into the tracked fixture. We use synthetic tokens here
    because the real dataset is intentionally local-only (under
    ``evals/tmp/``).
    """
    forbidden_tokens = ["FORBIDDEN_REAL_TOKEN"]
    cases = [
        _make_case(
            case_id="syn-clean",
            source_kind="synthetic_short",
            article_text="a clean synthetic body",
        ),
    ]
    dataset_dir = _write_dataset(tmp_path, cases)
    dataset = load_r4_a3_dataset(dataset_dir)

    synthetic_cases = [
        c for c in dataset.cases if c.source_kind.startswith("synthetic")
    ]
    assert synthetic_cases, "expected at least one synthetic case"
    for case in synthetic_cases:
        text = case.article_text or ""
        for token in forbidden_tokens:
            assert token not in text, (
                f"synthetic case {case.id} article_text must not contain "
                f"{token!r}"
            )


# ---------------------------------------------------------------------------
# Schema validation tests — pure model validation, no disk I/O
# ---------------------------------------------------------------------------


def test_required_field_missing_raises() -> None:
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Case.model_validate(
            {
                "source_kind": "synthetic_short",
                "input_mode": "manual",
                "source_metadata": "unknown",
                "baseline_mode": "complete",
                "question": "test",
                "question_category": "main_idea",
                "expected": {},
            }
        )


def test_expected_block_round_trip() -> None:
    """Smoke-check that the Expected model survives a round trip too."""
    expected = ReaderRecordAskR4A3Expected(
        expected_entity_set={"city": ["CityA"]},
        allowed_temporal_claims=["2026"],
        allowed_numerics=["42"],
        requested_count=1,
        requested_count_kind="exercise_items",
        must_declare_no_year=False,
    )
    rebuilt = ReaderRecordAskR4A3Expected.model_validate_json(
        expected.model_dump_json()
    )
    assert rebuilt == expected


def test_missing_dataset_dir_raises(tmp_path: Path) -> None:
    """Loader must fail closed when the dataset dir does not exist."""
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ReaderRecordAskDatasetLoadError) as exc_info:
        load_r4_a3_dataset(missing)
    assert "dataset directory not found" in str(exc_info.value)


def test_missing_dataset_yaml_raises(tmp_path: Path) -> None:
    """Loader must fail closed when dataset.yaml is absent."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ReaderRecordAskDatasetLoadError) as exc_info:
        load_r4_a3_dataset(empty_dir)
    assert "dataset.yaml not found" in str(exc_info.value)


def test_legacy_required_article_facts_migrated(tmp_path: Path) -> None:
    """Legacy ``required_article_facts`` field auto-converts to atomic_facts."""
    expected = ReaderRecordAskR4A3Expected(
        required_article_facts=["The sky is blue.", "Water is wet."],
    )
    case = _make_case(case_id="legacy", expected=expected)
    dataset_dir = _write_dataset(tmp_path, [case])
    dataset = load_r4_a3_dataset(dataset_dir)

    loaded = dataset.cases[0]
    assert len(loaded.expected.atomic_facts) == 2
    assert loaded.expected.atomic_facts[0].fact_id == "legacy-0"
    assert loaded.expected.atomic_facts[0].answer_alias_groups == [
        ["The sky is blue."]
    ]
    assert loaded.expected.atomic_facts[1].fact_id == "legacy-1"
    assert loaded.expected.atomic_facts[1].answer_alias_groups == [
        ["Water is wet."]
    ]
