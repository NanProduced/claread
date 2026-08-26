"""P-5D-R3: shared refinement addressing + frozen derivation-field pre-check."""

from __future__ import annotations

import inspect

from app.schemas.internal.daily_lesson_v2 import BlueprintDraft
from app.services.daily_reader.teaching.prototype import build_semantic_review_prompt
from app.services.daily_reader.teaching.refinement_addressing import (
    BLUEPRINT_ONLY_FIELDS,
    DUAL_CONTAINER_FIELDS,
    PACKAGE_ONLY_FIELDS,
    REVIEW_FROZEN_DERIVATION_CONTRACT,
    collect_fields_to_fix,
    preapply_patch_violations,
)
from app.services.daily_reader.workflow import refinement_node
from app.services.prompting.prompt_loader import load_agent_instructions


def _blueprint() -> dict:
    return {
        "article_type": "news_report",
        "effective_difficulty": "B1",
        "title_zh": "标题",
        "subtitle_zh": "副题",
        "tags_zh": ["标签一", "标签二"],
        "reading_mission": "带着问题阅读。",
        "reading_mission_stance": "neutral",
        "learning_objectives": ["抓住事实链"],
        "structure_map": [{"label": "Lead", "function": "open", "paragraph_ids": ["u01"]}],
        "selected_paragraph_ids": ["u01", "u02"],
        "comprehension_checkpoints": [
            {
                "skill": "fact_location",
                "prompt_subject": "时机",
                "evidence_paragraph_ids": ["u01"],
            }
        ],
        "transfer_task": {"task_kind": "retell", "prompt": "复述。"},
    }


def _package() -> dict:
    return {
        "comprehension_checkpoints": _blueprint()["comprehension_checkpoints"],
        "high_difficulty_unit_ids": ["u02"],
        "language_targets": [{"paragraph_id": "u01", "meaning_zh": "义", "usage_note": "用法"}],
        "sentence_maps": [{"paragraph_id": "u01", "teaching_purpose": "主干"}],
        "transfer_task": {"task_kind": "retell", "prompt": "复述。"},
        "translations_by_paragraph_id": {"u01": "译文。"},
    }


def _issue(field: str) -> dict[str, str]:
    return {"contract": "difficulty_fit", "field": field, "problem": "needs a directed fix"}


def test_content_field_collects_top_level_key() -> None:
    fields, code, raw = collect_fields_to_fix(
        [_issue("learning_package.language_targets[0].meaning_zh")],
        _package(),
        _blueprint(),
    )
    assert code is None
    assert raw is None
    assert list(fields) == ["language_targets"]


def test_effective_difficulty_is_frozen_derivation_field() -> None:
    fields, code, raw = collect_fields_to_fix(
        [_issue("blueprint.effective_difficulty")],
        _package(),
        _blueprint(),
    )
    assert fields == {}
    assert code == "frozen_derivation_field"
    assert raw == "blueprint.effective_difficulty"


def test_high_difficulty_unit_ids_is_frozen_derivation_field() -> None:
    _, code, raw = collect_fields_to_fix(
        [_issue("learning_package.high_difficulty_unit_ids")],
        _package(),
        _blueprint(),
    )
    assert code == "frozen_derivation_field"
    assert raw == "learning_package.high_difficulty_unit_ids"


def test_nested_paragraph_id_and_evidence_ids_are_frozen() -> None:
    for field in (
        "language_targets[0].paragraph_id",
        "learning_package.sentence_maps[0].paragraph_id",
        "blueprint.comprehension_checkpoints[0].evidence_paragraph_ids",
    ):
        _, code, raw = collect_fields_to_fix([_issue(field)], _package(), _blueprint())
        assert code == "frozen_derivation_field", field
        assert raw == field


def test_identity_fields_not_in_derive_are_not_frozen() -> None:
    for field in ("article_type", "blueprint.selected_paragraph_ids"):
        fields, code, _ = collect_fields_to_fix([_issue(field)], _package(), _blueprint())
        assert code is None, field
        assert fields


def test_unknown_still_wins_over_frozen() -> None:
    _, code, raw = collect_fields_to_fix(
        [_issue("learning_package.effective_difficulty")],
        _package(),
        _blueprint(),
    )
    assert code == "refinement_field_unknown"
    assert raw == "learning_package.effective_difficulty"


def test_patch_changing_nested_paragraph_id_is_frozen_violation() -> None:
    package = _package()
    patched = [
        {**package["language_targets"][0], "paragraph_id": "u02"},
    ]
    violations = preapply_patch_violations(
        {"language_targets": patched},
        package,
        _blueprint(),
        {"language_targets": package["language_targets"]},
    )
    assert violations == [
        {
            "container": "learning_package",
            "error_type": "frozen_derivation_field",
            "loc": ["language_targets", "paragraph_id"],
        }
    ]


def test_content_only_language_target_patch_is_not_frozen() -> None:
    package = _package()
    patched = [{**package["language_targets"][0], "meaning_zh": "更具体的释义"}]
    violations = preapply_patch_violations(
        {"language_targets": patched},
        package,
        _blueprint(),
        {"language_targets": package["language_targets"]},
    )
    assert violations == []


def test_ownership_table_matches_blueprint_and_package_keys() -> None:
    assert set(BLUEPRINT_ONLY_FIELDS) | set(DUAL_CONTAINER_FIELDS) == set(
        BlueprintDraft.model_fields
    )
    assert set(PACKAGE_ONLY_FIELDS) | set(DUAL_CONTAINER_FIELDS) == {
        "comprehension_checkpoints",
        "high_difficulty_unit_ids",
        "language_targets",
        "sentence_maps",
        "transfer_task",
        "translations_by_paragraph_id",
    }


def test_review_prompt_and_registry_declare_frozen_fields_and_ownership() -> None:
    prompt = build_semantic_review_prompt("body.", _blueprint(), _package(), {})
    registry = load_agent_instructions("daily_semantic_review")
    assert REVIEW_FROZEN_DERIVATION_CONTRACT in prompt
    assert REVIEW_FROZEN_DERIVATION_CONTRACT in registry
    for token in (
        "article_type",
        "selected_paragraph_ids",
        "translations_by_paragraph_id",
        "comprehension_checkpoints",
        "transfer_task",
        "lesson_blueprint",
    ):
        assert token in REVIEW_FROZEN_DERIVATION_CONTRACT


def test_production_and_evals_runner_share_addressing_implementation() -> None:
    from pathlib import Path

    workflow_src = inspect.getsource(refinement_node)
    assert "collect_fields_to_fix" in workflow_src
    assert "preapply_patch_violations" in workflow_src
    runner_path = (
        Path(__file__).resolve().parents[3]
        / "evals"
        / "scripts"
        / "run_daily_reader_teaching_prototype.py"
    )
    runner_src = runner_path.read_text(encoding="utf-8")
    assert "collect_fields_to_fix" in runner_src
    assert "preapply_patch_violations" in runner_src
    assert "def root_of(" not in runner_src
