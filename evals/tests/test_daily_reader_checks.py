"""Unit tests for the daily-reader regression deterministic checks (A-6)."""

from __future__ import annotations

from pathlib import Path

import yaml

from claread_eval.daily_reader.checks import (
    check_gold_expression_coverage,
    check_highlight_dedup,
    check_no_boilerplate,
    check_translation_consistency,
    normalize_expression,
    run_deterministic_checks,
)
from claread_eval.daily_reader.judge import resolve_judge_config

EVALS_ROOT = Path(__file__).resolve().parents[1]
RUBRIC_PATH = EVALS_ROOT / "rubrics" / "daily-reader-regression-v1.yaml"


def _artifact(**overrides):
    base = {
        "title": "t",
        "difficulty": "B2",
        "original_text": "clean original",
        "body_json": {"paragraphs": [
            {"id": "p_0", "text": "clean paragraph text", "highlights": [],
             "reading_note": {"focus_question": "q", "micro_summary": "s",
                              "translation": "干净的段落译文。"}},
        ]},
        "highlights_json": [
            {"text": "clean phrase", "gloss": "干净的表达", "paragraph_id": "p_0"},
        ],
        "paragraph_notes_json": {
            "article_summary": "概述",
            "reading_focus": [],
            "notes": [{"paragraph_id": "p_0", "focus_question": "q",
                       "micro_summary": "s",
                       "translation": "干净的段落译文。这一句被长难句引用。"}],
        },
        "takeaways_json": {
            "article_takeaway": "收获",
            "key_expressions": [],
            "sentence_notes": [
                {"sentence": "This sentence is quoted.", "paragraph_id": "p_0",
                 "translation": "这一句被长难句引用。", "breakdown": "b", "takeaway": "t"},
            ],
            "writing_moves": [],
            "discussion_questions": ["q1", "q2"],
        },
    }
    base.update(overrides)
    return base


CASE = {
    "case_id": "unit",
    "gold": {
        "expected_difficulty": "B2",
        "dirty_fragments": ["- Published", "Copyright © 2026 NPR"],
        "expected_expressions": ["clean phrase"],
    },
}


def test_normalize_expression_collapses_plurals_and_case():
    assert normalize_expression("Manifestos") == normalize_expression("manifesto")
    assert normalize_expression("initiating") == normalize_expression("initiate")
    assert normalize_expression("  Push   BACK ") == "push back"
    # 'ss' endings are not stripped
    assert normalize_expression("class") == "class"


def test_no_boilerplate_passes_on_clean_artifact():
    result = check_no_boilerplate(CASE, _artifact())
    assert result["passed"] is True


def test_no_boilerplate_fails_when_fragment_leaks_into_translation():
    art = _artifact()
    art["paragraph_notes_json"]["notes"][0]["translation"] = (
        "干净的段落译文。Copyright © 2026 NPR 版权所有。这一句被长难句引用。"
    )
    result = check_no_boilerplate(CASE, art)
    assert result["passed"] is False
    assert "Copyright © 2026 NPR" in result["detail"]["hits"]


def test_highlight_dedup_fails_on_manifesto_regression():
    art = _artifact(highlights_json=[
        {"text": "manifesto", "gloss": "宣言", "paragraph_id": "p_0"},
        {"text": "Manifestos", "gloss": "宣言们", "paragraph_id": "p_1"},
        {"text": "manifesto", "gloss": "宣言", "paragraph_id": "p_2"},
    ])
    result = check_highlight_dedup({}, art)
    assert result["passed"] is False
    key = normalize_expression("manifesto")
    assert result["detail"]["duplicate_keys"][key] == 3


def test_highlight_dedup_passes_when_unique():
    assert check_highlight_dedup({}, _artifact())["passed"] is True


def test_translation_consistency_detects_retranslation():
    art = _artifact()
    art["takeaways_json"]["sentence_notes"][0]["translation"] = "这句被重新翻译了。"
    result = check_translation_consistency({}, art)
    assert result["passed"] is False
    assert len(result["detail"]["diffs"]) == 1
    assert result["detail"]["diffs"][0]["paragraph_id"] == "p_0"


def test_translation_consistency_whitespace_insensitive():
    art = _artifact()
    art["takeaways_json"]["sentence_notes"][0]["translation"] = "这一句被长难句 引用 。"
    assert check_translation_consistency({}, art)["passed"] is True


def test_gold_expression_coverage_threshold():
    case = {"gold": {"expected_expressions": ["clean phrase", "missing one"]}}
    result = check_gold_expression_coverage(case, _artifact())
    assert result["detail"]["coverage"] == 0.5
    assert result["passed"] is True
    case = {"gold": {"expected_expressions": ["a", "b", "clean phrase"]}}
    assert check_gold_expression_coverage(case, _artifact())["passed"] is False


def test_run_deterministic_checks_full_ratio():
    result = run_deterministic_checks(CASE, _artifact())
    assert result["total"] == 4
    assert result["passed"] == 4
    assert result["pass_ratio"] == 1.0


def test_aborted_artifact_marks_gold_coverage_na():
    # Transcript-rejection case: workflow aborts, nothing to evaluate.
    art = _artifact(highlights_json=[], takeaways_json={}, abort=True)
    result = check_gold_expression_coverage(CASE, art)
    assert result["passed"] is None


def test_aborted_artifact_ratio_excludes_na_check():
    art = _artifact(highlights_json=[], takeaways_json={}, abort=True)
    result = run_deterministic_checks(CASE, art)
    # gold_expression_coverage is n/a; the remaining 3 checks all pass.
    assert result["total"] == 3
    assert result["passed"] == 3
    assert result["pass_ratio"] == 1.0
    assert result["checks"]["gold_expression_coverage"]["passed"] is None


def test_non_aborted_empty_artifact_still_fails_coverage():
    art = _artifact(highlights_json=[], takeaways_json={})
    result = check_gold_expression_coverage(CASE, art)
    assert result["passed"] is False


def test_dataset_cases_are_well_formed():
    dataset_dir = EVALS_ROOT / "datasets" / "daily-reader-regression-v1"
    manifest = yaml.safe_load((dataset_dir / "dataset.yaml").read_text(encoding="utf-8"))
    assert manifest["id"] == "daily-reader-regression-v1"
    import json
    cases = sorted((dataset_dir / "cases").glob("*.json"))
    assert len(cases) == 5
    difficulties = set()
    for p in cases:
        case = json.loads(p.read_text(encoding="utf-8"))
        assert case["case_id"] == p.stem
        assert case["gold"]["expected_difficulty"]
        assert case["input"]["original_text"].strip()
        assert len(case["gold"]["expected_expressions"]) >= 3
        difficulties.add(case["gold"]["expected_difficulty"])
    # gold set spans difficulty bands and carries a dirty trap
    assert {"B1", "B2", "C1"} <= difficulties
    trap = json.loads(
        (dataset_dir / "cases" / "syn-dirty-trap-fridge.json").read_text(encoding="utf-8")
    )
    assert any("external" in f for f in trap["gold"]["dirty_fragments"])
    assert any("Copyright" in f for f in trap["gold"]["dirty_fragments"])


def test_rubric_contract_is_consistent():
    rubric = yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))
    det_ids = [c["id"] for c in rubric["deterministic_checks"]]
    assert det_ids == [
        "no_boilerplate", "highlight_dedup",
        "translation_consistency", "gold_expression_coverage",
    ]
    judge_ids = [d["id"] for d in rubric["judge"]["dimensions"]]
    assert judge_ids == [
        "vocab_difficulty_match", "sentence_note_complexity",
        "title_zh_quality", "learning_value",
    ]


def test_judge_fail_closed_without_gate(monkeypatch):
    monkeypatch.delenv("CLAREAD_ALLOW_REAL_LLM_TESTS", raising=False)
    rubric = yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))
    assert resolve_judge_config(rubric) is None


def test_judge_config_resolves_with_gate_and_key(monkeypatch):
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.delenv("CLAREAD_EVAL_JUDGE_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    rubric = yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))
    cfg = resolve_judge_config(rubric)
    assert cfg is not None
    assert cfg["base_url"] == "https://api.deepseek.com/v1"
    assert cfg["model"] == "deepseek-v4-pro"
