"""Unit tests for the shared teaching-contract package (P-5A).

The deterministic defense lines single-sourced under
``app.services.daily_reader.teaching`` must behave exactly like the evals
stack that imports them: these tests pin the runtime side independently
of the evals project (stdlib-only package, no provider/DB/network).
"""

from __future__ import annotations

import pytest

from app.services.daily_reader.teaching.contract import (
    TRANSFER_TASK_KIND_BY_ARTICLE_TYPE,
    validate_teaching_contract,
)
from app.services.daily_reader.teaching.gates import HARD_GATES, run_hard_gates
from app.services.daily_reader.teaching.normalize import (
    normalize_expression,
    normalize_text,
)
from app.services.daily_reader.teaching.schema import validate_artifact


def _case() -> dict:
    return {
        "case_id": "fx-b1",
        "input": {
            "source_caption": "",
            "reading_units": [
                {"id": "u01", "text": "The town opened a new library on Saturday."},
                {"id": "u02", "text": "Many families came to the opening event."},
            ],
        },
    }


def _artifact() -> dict:
    return {
        "case_id": "fx-b1",
        "run_meta": {"outcome": "cleaned_publish", "refinement_count": 0},
        "lesson_blueprint": {
            "article_type": "news_report",
            "effective_difficulty": "B1",
            "reading_mission": "带着问题读这篇短新闻。",
            "learning_objectives": ["抓住时间地点事实"],
            "structure_map": [
                {"label": "事件", "paragraph_ids": ["u01"], "function": "opening"},
                {"label": "反应", "paragraph_ids": ["u02"], "function": "detail"},
            ],
            "selected_paragraph_ids": ["u01"],
        },
        "learning_package": {
            "comprehension_checkpoints": [
                {
                    "prompt": "图书馆什么时候开放？",
                    "skill": "fact_location",
                    "evidence_paragraph_ids": ["u01"],
                    "answer_evidence_paragraph_ids": ["u01"],
                },
                {
                    "prompt": "谁参加了开放活动？",
                    "skill": "fact_location",
                    "evidence_paragraph_ids": ["u02"],
                    "answer_evidence_paragraph_ids": ["u02"],
                },
            ],
            "language_targets": [
                {
                    "expression": "opened a new library",
                    "meaning_zh": "开了一家新图书馆",
                    "usage_note": "过去时动词短语",
                    "reusable_pattern": "open sth",
                    "target_kind": "verb_phrase",
                    "teaching_purpose": "动词短语",
                    "paragraph_id": "u01",
                },
                {
                    "expression": "came to",
                    "meaning_zh": "前来",
                    "usage_note": "不及物短语",
                    "reusable_pattern": "come to sth",
                    "target_kind": "phrasal_verb",
                    "teaching_purpose": "动词短语",
                    "paragraph_id": "u02",
                },
                {
                    "expression": "opening event",
                    "meaning_zh": "开放活动",
                    "usage_note": "名词短语",
                    "reusable_pattern": "the opening of ...",
                    "target_kind": "noun_phrase",
                    "teaching_purpose": "名词短语",
                    "paragraph_id": "u02",
                },
            ],
            "sentence_maps": [
                {
                    "sentence": "The town opened a new library on Saturday.",
                    "structure_zh": "简单过去时陈述句",
                    "translation": "镇上周六开了一家新图书馆。",
                    "teaching_purpose": "时态示范",
                    "paragraph_id": "u01",
                },
            ],
            "translations_by_paragraph_id": {
                "u01": "镇上周六开了一家新图书馆。",
                "u02": "许多家庭前来参加开放活动。",
            },
            "transfer_task": {
                "task_kind": "retell",
                "content_requirement": "fact_chain",
                "required_language_target_expressions": ["opened a new library"],
            },
        },
        "source_assets": {},
    }


def test_runtime_gate_registry_is_the_nine_gold_free_gates() -> None:
    assert list(HARD_GATES) == [
        "anchors_resolve",
        "expression_explained_once",
        "counts_in_bounds",
        "no_empty_placeholders",
        "checkpoint_evidence_valid",
        "sentence_map_translation_reuse",
        "source_caption_preserved",
        "refinement_bounded",
        "legacy_fields_not_required",
    ]


def test_green_artifact_passes_all_runtime_gates() -> None:
    result = run_hard_gates(_case(), _artifact())
    assert result["all_passed"] is True
    assert result["passed_count"] == result["scored_count"] == len(HARD_GATES)


def test_unresolvable_anchor_fails_anchors_resolve() -> None:
    artifact = _artifact()
    artifact["lesson_blueprint"]["selected_paragraph_ids"] = ["u99"]
    detail = run_hard_gates(_case(), artifact)["gates"]["anchors_resolve"]
    assert detail["passed"] is False
    assert any(p["anchor"] == "u99" for p in detail["detail"]["unresolved"])


def test_reject_run_marks_teaching_gates_na() -> None:
    artifact = _artifact()
    artifact["learning_package"] = {}
    artifact["run_meta"] = {"outcome": "reject"}
    result = run_hard_gates(_case(), artifact)
    assert result["gates"]["counts_in_bounds"]["passed"] is None
    assert result["scored_count"] < len(HARD_GATES)


def test_validate_artifact_shape_is_gold_free() -> None:
    case = _case()  # no gold at all — production shape
    artifact = _artifact()
    artifact["lesson_blueprint"]["article_type"] = "essay_five"
    errs = validate_artifact(case, artifact)
    assert any("article_type" in e for e in errs)
    # no gold-equality errors can appear because there is no gold
    assert not any("gold." in e for e in errs)


def test_normalize_text_matches_evals_semantics() -> None:
    assert normalize_text("  the   TOWN\topen ") == "the town open"
    assert normalize_text("") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Manifestos", "manifesto"), ("Initiating", "initiat"), ("  the   TOWN ", "the town")],
)
def test_normalize_expression_matches_evals_semantics(raw: str, expected: str) -> None:
    assert normalize_expression(raw) == expected


def test_transfer_mapping_is_frozen() -> None:
    assert TRANSFER_TASK_KIND_BY_ARTICLE_TYPE == {
        "news_report": "retell",
        "opinion_commentary": "counter",
        "explainer": "explain",
        "narrative_profile": "rewrite",
    }


def test_contract_flags_source_echo_and_ungrounded_tokens() -> None:
    unit_text = "At least 5,700 excess deaths were recorded."
    units = [{"id": "u01", "text": unit_text}]
    blueprint = {
        "article_type": "news_report",
        "effective_difficulty": "B1",
        "reading_mission_stance": "neutral",
    }
    base_pkg = {
        "language_targets": [
            {
                "expression": "excess deaths",
                "target_kind": "x",
                "teaching_purpose": "p1",
                "paragraph_id": "u01",
            },
            {
                "expression": "5,700 excess",
                "target_kind": "x",
                "teaching_purpose": "p2",
                "paragraph_id": "u01",
            },
            {
                "expression": "were recorded",
                "target_kind": "x",
                "teaching_purpose": "p3",
                "paragraph_id": "u01",
            },
        ],
        "sentence_maps": [
            {
                "sentence": unit_text,
                "structure_zh": "s",
                "translation": "t",
                "teaching_purpose": "p4",
                "paragraph_id": "u01",
            },
        ],
        "comprehension_checkpoints": [
            {"prompt_subject": "a", "reference_answer_subject": "b"},
            {"prompt_subject": "c", "reference_answer_subject": "d"},
        ],
        "transfer_task": {
            "task_kind": "retell",
            "content_requirement": "fact_chain",
            "required_language_target_expressions": ["excess deaths"],
        },
    }
    issues = validate_teaching_contract(
        blueprint,
        {**base_pkg, "translations_by_paragraph_id": {"u01": "至少有5700例超额死亡。"}},
        reading_units=units,
    )
    assert issues == []

    echo = validate_teaching_contract(
        blueprint,
        {
            **base_pkg,
            "translations_by_paragraph_id": {"u01": "At least 5,700 excess deaths were recorded."},
        },
        reading_units=units,
    )
    codes = {issue["code"] for issue in echo}
    assert "translation_source_echo" in codes

    fabricated = validate_teaching_contract(
        blueprint,
        {**base_pkg, "translations_by_paragraph_id": {"u01": "记录了至少2022例。"}},
        reading_units=units,
    )
    assert any(i["code"] == "translation_source_mismatch" for i in fabricated)


def test_shared_package_is_stdlib_only() -> None:
    import ast
    from pathlib import Path

    pkg = Path(__file__).parent / "app" / "services" / "daily_reader" / "teaching"
    forbidden = {"pydantic", "httpx", "sqlalchemy", "redis", "langchain"}
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert not (roots & forbidden), f"{py.name} imports {roots & forbidden}"
