"""Unit tests for the Daily Reader teaching-contract v2 eval stack (P-2).

Contract source: tmp/daily-reader-optimization/prompt-p2-eval-v2.md +
p1-teaching-contract-v2.md §9.1/§9.2/§9.3. Everything here runs fully
offline: no provider, no DB, no network.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from claread_eval.daily_reader.checks import run_deterministic_checks
from claread_eval.daily_reader.teaching_v2 import gates as g2
from claread_eval.daily_reader.teaching_v2 import judge as j2
from claread_eval.daily_reader.teaching_v2 import report as rp
from claread_eval.daily_reader.teaching_v2 import review as rv
from claread_eval.daily_reader.teaching_v2 import schema as sc

EVALS_ROOT = Path(__file__).resolve().parents[1]
RUBRIC_PATH = EVALS_ROOT / "rubrics" / "daily-reader-teaching-v2.yaml"
DATASET_DIR = EVALS_ROOT / "datasets" / "daily-reader-teaching-v2"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "daily_reader_teaching_v2"


# ---------------------------------------------------------------------------
# In-memory case/artifact builders (synthetic; only used to exercise gates)
# ---------------------------------------------------------------------------


def _b1_case(**gold_over) -> dict:
    case = {
        "schema_version": 2,
        "case_id": "fx-b1",
        "dataset_id": "daily-reader-teaching-v2",
        "origin": {"kind": "frozen_real_article", "source": "bbc",
                   "source_url": "https://example.test/a",
                   "captured_at": "2026-08-21", "frozen_real_article": True},
        "input": {
            "title": "Small town opens new library",
            "subtitle": "The library opens on Saturday.",
            "source": "bbc",
            "source_url": "https://example.test/a",
            "original_text": ("The town opened a new library on Saturday.\n"
                              "Many families came to the opening event.\n"
                              "The mayor said books help children learn.\n"
                              "The library will stay open until nine at night."),
            "reading_units": [
                {"id": "u01", "text": "The town opened a new library on Saturday."},
                {"id": "u02", "text": "Many families came to the opening event."},
                {"id": "u03", "text": "The mayor said books help children learn."},
                {"id": "u04", "text": "The library will stay open until nine at night."},
            ],
            "source_caption": "The new library in the town centre.",
        },
        "gold": {
            "annotation_status": "DRAFT_PM_REVIEW",
            "expected_outcome": "cleaned_publish",
            "expected_difficulty": "B1",
            "article_type": "news_report",
            "dirty_fragments": [],
            "rejection_reasons": [],
            "key_evidence": [
                {"source_quote": "The town opened a new library on Saturday",
                 "acceptable_answer_points_zh": ["新图书馆周六开放"],
                 "paragraph_ids": ["u01"]},
                {"source_quote": "The mayor said books help children learn",
                 "acceptable_answer_points_zh": ["市长认为书籍帮助儿童学习"],
                 "paragraph_ids": ["u03"]},
            ],
            "core_expressions": [
                {"expression": "stay open", "source_quote": "stay open until nine",
                 "meaning_zh": "保持开放", "teaching_value": "常用搭配",
                 "paragraph_ids": ["u04"]},
            ],
            "forbidden_facts": [
                {"claim_zh": "图书馆周日开放", "reason": "原文为周六"}],
            "acceptable_transfer_directions": [
                {"task_kind": "retell", "required_learning_target": "stay open",
                 "acceptable_direction_zh": "用 stay open 复述开放时间"}],
            "expected_translation_coverage": {
                "policy": "all_units",
                "required_paragraph_ids": ["u01", "u02", "u03", "u04"],
                "allowed_paragraph_ids": ["u01", "u02", "u03", "u04"],
            },
        },
    }
    case["gold"].update(gold_over)
    return case


def _b1_artifact(**over) -> dict:
    art = {
        "case_id": "fx-b1",
        "lesson_blueprint": {
            "article_type": "news_report",
            "effective_difficulty": "B1",
            "reading_mission": "带着‘新图书馆为社区带来什么’的问题读这篇短新闻。",
            "learning_objectives": ["抓住新闻中的时间、地点和人物事实"],
            "structure_map": [
                {"label": "事件", "paragraph_ids": ["u01", "u02"], "function": "opening"},
                {"label": "观点与安排", "paragraph_ids": ["u03", "u04"], "function": "detail"},
            ],
            "selected_paragraph_ids": ["u01", "u03"],
        },
        "learning_package": {
            "comprehension_checkpoints": [
                {"prompt": "新图书馆什么时候开放？", "skill": "fact_location",
                 "evidence_paragraph_ids": ["u01"],
                 "answer_evidence_paragraph_ids": ["u01"],
                 "reference_answer": "周六开放。",
                 "explanation_zh": "第一段第一句直接给出时间。"},
                {"prompt": "市长认为书有什么作用？", "skill": "fact_location",
                 "evidence_paragraph_ids": ["u03"],
                 "answer_evidence_paragraph_ids": ["u03"],
                 "reference_answer": "帮助儿童学习。",
                 "explanation_zh": "第三段引述市长原话。"},
            ],
            "language_targets": [
                {"expression": "stay open", "paragraph_id": "u04",
                 "meaning_zh": "保持开放", "usage_note": "描述营业/开放时间的常用搭配",
                 "reusable_pattern": "stay open until + 时间"},
                {"expression": "opening event", "paragraph_id": "u02",
                 "meaning_zh": "开幕活动", "usage_note": "名词修饰名词",
                 "reusable_pattern": "opening + 名词"},
                {"expression": "help children learn", "paragraph_id": "u03",
                 "meaning_zh": "帮助儿童学习", "usage_note": "help + 人 + 动词原形",
                 "reusable_pattern": "help sb do sth"},
            ],
            "sentence_maps": [
                {"sentence": "The town opened a new library on Saturday.",
                 "paragraph_id": "u01",
                 "structure_zh": "主语(The town)+谓语(opened)+宾语(a new library)+时间状语",
                 "translation": "小镇在周六开设了一家新图书馆。"},
            ],
            "translations_by_paragraph_id": {
                "u01": "小镇在周六开设了一家新图书馆。",
                "u02": "许多家庭来到开幕活动现场。",
                "u03": "市长说，书籍帮助儿童学习。",
                "u04": "图书馆将保持开放到晚上九点。",
            },
            "post_read_summary": "小镇周六开放新图书馆，开幕活动吸引许多家庭，"
                                 "市长强调阅读对儿童的价值。",
            "transfer_task": {"task_kind": "retell",
                              "prompt": "用 stay open 复述图书馆的开放时间。",
                              "scaffold": "The library stays open until ...",
                              "reference_points": ["周六开放", "开放到晚上九点"]},
        },
        "source_assets": {"source_caption": "The new library in the town centre."},
        "run_meta": {"outcome": "cleaned_publish", "abort": False,
                     "refinement_count": 0, "usage": None},
    }
    art.update(over)
    return art


def _b2_case() -> dict:
    case = _b1_case()
    case["case_id"] = "fx-b2"
    case["gold"]["expected_difficulty"] = "B2"
    case["gold"]["expected_translation_coverage"] = {
        "policy": "selected_units",
        "required_paragraph_ids": ["u01"],
        "allowed_paragraph_ids": ["u01", "u03", "u04"],
    }
    return case


def _b2_artifact() -> dict:
    art = _b1_artifact()
    art["case_id"] = "fx-b2"
    art["lesson_blueprint"]["effective_difficulty"] = "B2"
    return art


def _c1_case() -> dict:
    case = _b1_case()
    case["case_id"] = "fx-c1"
    case["gold"]["expected_difficulty"] = "C1"
    case["gold"]["article_type"] = "opinion_commentary"
    case["gold"]["expected_translation_coverage"] = {
        "policy": "selected_units",
        "required_paragraph_ids": ["u03"],
        "allowed_paragraph_ids": ["u03", "u04"],
    }
    return case


def _c1_artifact() -> dict:
    """C1: only explicitly selected hard units translated; plain checkpoint
    evidence (u01) has NO translation and must not fail."""
    art = _b1_artifact()
    art["case_id"] = "fx-c1"
    art["lesson_blueprint"]["effective_difficulty"] = "C1"
    art["lesson_blueprint"]["article_type"] = "opinion_commentary"
    art["lesson_blueprint"]["selected_paragraph_ids"] = ["u03"]
    art["learning_package"]["translations_by_paragraph_id"] = {
        "u03": "市长说，书籍帮助儿童学习。",
    }
    return art


def _reject_case() -> dict:
    case = _b1_case()
    case["case_id"] = "fx-reject"
    case["gold"]["expected_outcome"] = "reject"
    case["gold"]["rejection_reasons"] = ["transcript_skeleton"]
    return case


def _reject_artifact() -> dict:
    return {"case_id": "fx-reject",
            "source_assets": {"source_caption": "The new library in the town centre."},
            "run_meta": {"outcome": "reject", "abort": True,
                         "rejection_reason": "transcript_skeleton",
                         "refinement_count": 0, "usage": None}}


def _run(case: dict, artifact: dict) -> dict:
    return g2.run_hard_gates(case, artifact)


def _gate(res: dict, gid: str) -> dict:
    return res["gates"][gid]


# ---------------------------------------------------------------------------
# v1 does not understand v2 artifacts (RED anchor)
# ---------------------------------------------------------------------------


def test_v1_checks_do_not_score_v2_artifact():
    """v1 is structurally blind to v2 artifacts: it only inspects v1
    surfaces (body_json/highlights_json/takeaways_json), none of which a
    v2 artifact carries — so a dirty leak inside a v2 surface is never
    seen and gold coverage is vacuous. v1 must never score v2 runs."""
    case = _b1_case()
    case["gold"]["dirty_fragments"] = ["- Published"]
    art = _b1_artifact()
    art["learning_package"]["post_read_summary"] = "- Published leaked into v2 surface"
    det = run_deterministic_checks(case, art)
    assert det["checks"]["no_boilerplate"]["passed"] is True  # v1 saw nothing
    assert det["checks"]["gold_expression_coverage"]["detail"].get("note") == \
        "no gold expressions"
    # the v2 stack catches the same leak through its own surface inventory
    res = g2.run_hard_gates(case, art)
    assert res["gates"]["no_boilerplate_residue"]["passed"] is False


# ---------------------------------------------------------------------------
# schema.py
# ---------------------------------------------------------------------------


def test_english_word_count_is_whitespace_split():
    assert sc.english_word_count("") == 0
    assert sc.english_word_count("one  two\nthree\tfour") == 4


def test_validate_case_flags_bad_enums_and_unresolved_anchor():
    case = _b1_case()
    assert sc.validate_case(case) == []
    bad = copy.deepcopy(case)
    bad["gold"]["article_type"] = "mixed"
    bad["gold"]["key_evidence"][0]["paragraph_ids"] = ["u99"]
    errs = sc.validate_case(bad)
    assert any("article_type" in e for e in errs)
    assert any("u99" in e for e in errs)


def test_validate_case_flags_source_quote_not_substring():
    bad = copy.deepcopy(_b1_case())
    bad["gold"]["key_evidence"][0]["source_quote"] = "opened a brand new library"
    errs = sc.validate_case(bad)
    assert any("source_quote" in e for e in errs)


def test_validate_case_flags_wrong_annotation_status():
    bad = copy.deepcopy(_b1_case())
    bad["gold"]["annotation_status"] = "human_approved"
    errs = sc.validate_case(bad)
    assert any("annotation_status" in e for e in errs)


def test_validate_artifact_flags_bad_fields():
    art = _b1_artifact()
    assert sc.validate_artifact(_b1_case(), art) == []
    bad = copy.deepcopy(art)
    bad["lesson_blueprint"]["effective_difficulty"] = "A2"
    bad["run_meta"]["outcome"] = "published"
    errs = sc.validate_artifact(_b1_case(), bad)
    assert any("effective_difficulty" in e for e in errs)
    assert any("outcome" in e for e in errs)


def test_dataset_coverage_matrix_validation():
    cases = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted((DATASET_DIR / "cases").glob("*.json"))]
    assert sc.validate_dataset_coverage(cases) == []


def test_dataset_coverage_matrix_gaps_detected():
    cases = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted((DATASET_DIR / "cases").glob("*.json"))]
    too_few = copy.deepcopy(cases)[:5]
    errs = sc.validate_dataset_coverage(too_few)
    assert any("8-12" in e for e in errs)
    no_b1 = [c for c in copy.deepcopy(cases)
             if c["gold"]["expected_difficulty"] != "B1"]
    errs = sc.validate_dataset_coverage(no_b1)
    assert any("B1" in e for e in errs)
    a2 = copy.deepcopy(cases)
    a2[0]["gold"]["expected_difficulty"] = "A2"
    errs = sc.validate_dataset_coverage(a2)
    assert any("A2" in e for e in errs)


# ---------------------------------------------------------------------------
# gates — green baseline + legacy-free artifact must pass (gate 12)
# ---------------------------------------------------------------------------


def test_all_hard_gates_pass_on_green_b1_artifact():
    res = _run(_b1_case(), _b1_artifact())
    failing = {gid: g for gid, g in res["gates"].items() if g["passed"] is False}
    assert failing == {}, f"unexpected gate failures: {failing}"
    assert res["all_passed"] is True


def test_legal_v2_artifact_without_legacy_fields_must_not_fail():
    art = _b1_artifact()
    assert "focus_question" not in json.dumps(art)
    assert "micro_summary" not in json.dumps(art)
    assert "discussion_questions" not in json.dumps(art)
    res = _run(_b1_case(), art)
    assert _gate(res, "legacy_fields_not_required")["passed"] is True


def test_gate_registry_has_exactly_12_ordered_gates():
    assert len(g2.HARD_GATES) == 12
    assert list(g2.HARD_GATES)[0] == "no_boilerplate_residue"


def test_gate_boilerplate_residue():
    art = _b1_artifact()
    case = _b1_case()
    case["gold"]["dirty_fragments"] = ["- Published"]
    art["learning_package"]["post_read_summary"] = "- Published 小镇开设图书馆。"
    assert _gate(_run(case, art), "no_boilerplate_residue")["passed"] is False


def test_gate_unresolvable_anchor():
    art = _b1_artifact()
    art["learning_package"]["language_targets"][0]["paragraph_id"] = "u99"
    assert _gate(_run(_b1_case(), art), "anchors_resolve")["passed"] is False


def test_gate_duplicate_expression():
    art = _b1_artifact()
    art["learning_package"]["language_targets"][1]["expression"] = "Stay  OPEN"
    assert _gate(_run(_b1_case(), art), "expression_explained_once")["passed"] is False


@pytest.mark.parametrize("path,count", [
    (("learning_package", "comprehension_checkpoints"), 5),
    (("learning_package", "language_targets"), 2),
    (("learning_package", "sentence_maps"), 3),
    (("lesson_blueprint", "learning_objectives"), 3),
    (("lesson_blueprint", "structure_map"), 1),
])
def test_gate_counts_out_of_bounds(path, count):
    art = _b1_artifact()
    node = art
    for key in path[:-1]:
        node = node[key]
    lst = node[path[-1]]
    if count > len(lst):
        lst.extend(copy.deepcopy(lst[0]) for _ in range(count - len(lst)))
        for i, item in enumerate(lst):
            if isinstance(item, dict) and "expression" in item:
                item["expression"] = f"expr variant {i}"
    else:
        del lst[count:]
    assert _gate(_run(_b1_case(), art), "counts_in_bounds")["passed"] is False


def test_gate_transfer_task_must_be_exactly_one():
    art = _b1_artifact()
    art["learning_package"]["transfer_task"] = None
    assert _gate(_run(_b1_case(), art), "counts_in_bounds")["passed"] is False


def test_gate_empty_placeholder():
    art = _b1_artifact()
    art["learning_package"]["language_targets"][0]["meaning_zh"] = ""
    assert _gate(_run(_b1_case(), art), "no_empty_placeholders")["passed"] is False
    art2 = _b1_artifact()
    art2["learning_package"]["post_read_summary"] = "{{TODO}}"
    assert _gate(_run(_b1_case(), art2), "no_empty_placeholders")["passed"] is False


def test_gate_answer_evidence_must_be_subset():
    art = _b1_artifact()
    art["learning_package"]["comprehension_checkpoints"][0][
        "answer_evidence_paragraph_ids"] = ["u02"]
    assert _gate(_run(_b1_case(), art), "checkpoint_evidence_valid")["passed"] is False
    art2 = _b1_artifact()
    art2["learning_package"]["comprehension_checkpoints"][0][
        "answer_evidence_paragraph_ids"] = []
    assert _gate(_run(_b1_case(), art2), "checkpoint_evidence_valid")["passed"] is False


def test_gate_sentence_map_translation_must_reuse_shared():
    art = _b1_artifact()
    art["learning_package"]["sentence_maps"][0]["translation"] = "城里周六开了图书馆。"
    assert _gate(_run(_b1_case(), art),
                 "sentence_map_translation_reuse")["passed"] is False


def test_gate_source_caption_overwritten():
    art = _b1_artifact()
    art["source_assets"]["source_caption"] = "AI 生成的图说。"
    assert _gate(_run(_b1_case(), art), "source_caption_preserved")["passed"] is False
    # empty source caption -> artifact must stay empty
    case = _b1_case()
    case["input"]["source_caption"] = None
    art2 = _b1_artifact()
    art2["source_assets"]["source_caption"] = "凭空补写的图说"
    assert _gate(_run(case, art2), "source_caption_preserved")["passed"] is False


def test_gate_refinement_over_one():
    art = _b1_artifact()
    art["run_meta"]["refinement_count"] = 2
    assert _gate(_run(_b1_case(), art), "refinement_bounded")["passed"] is False


def test_gate_outcome_mismatch():
    case = _b1_case()
    art = _reject_artifact()
    art["case_id"] = "fx-b1"
    assert _gate(_run(case, art), "outcome_matches_gold")["passed"] is False
    res = _run(_reject_case(), _b1_artifact())
    assert _gate(res, "outcome_matches_gold")["passed"] is False


def test_gate_reject_case_passes_when_rejected():
    res = _run(_reject_case(), _reject_artifact())
    assert res["all_passed"] is True
    # teaching-package gates are n/a, not failing
    assert _gate(res, "counts_in_bounds")["passed"] is None


# ---------------------------------------------------------------------------
# gate 11 — translation coverage policy dispatch
# ---------------------------------------------------------------------------


def test_gate_b1_missing_unit_translation():
    art = _b1_artifact()
    del art["learning_package"]["translations_by_paragraph_id"]["u02"]
    g = _gate(_run(_b1_case(), art), "translation_coverage_policy")
    assert g["passed"] is False
    assert "u02" in json.dumps(g["detail"], ensure_ascii=False)


def test_gate_b1_same_key_still_one_shared_translation():
    art = _b1_artifact()
    art["learning_package"]["translations_by_paragraph_id"]["u03"] = "另一份译文。"
    # dict semantics: same key => still exactly one shared translation
    assert _gate(_run(_b1_case(), art), "translation_coverage_policy")["passed"] is True


def test_gate_b2_outside_allowed_fails():
    art = _b2_artifact()
    art["learning_package"]["translations_by_paragraph_id"]["u02"] = "范围外译文。"
    assert _gate(_run(_b2_case(), art), "translation_coverage_policy")["passed"] is False


def test_gate_b2_missing_required_fails():
    art = _b2_artifact()
    del art["learning_package"]["translations_by_paragraph_id"]["u01"]
    assert _gate(_run(_b2_case(), art), "translation_coverage_policy")["passed"] is False


def test_gate_b2_associated_units_need_translation():
    case = _b2_case()
    case["gold"]["expected_translation_coverage"]["allowed_paragraph_ids"] = [
        "u01", "u02", "u03", "u04"]
    art = _b2_artifact()
    del art["learning_package"]["translations_by_paragraph_id"]["u04"]
    assert _gate(_run(case, art), "translation_coverage_policy")["passed"] is False


def test_gate_c1_plain_checkpoint_evidence_does_not_force_translation():
    # u01 is checkpoint evidence but NOT in gold selected units -> C1 does not
    # force a translation for it.
    res = _run(_c1_case(), _c1_artifact())
    assert _gate(res, "translation_coverage_policy")["passed"] is True


def test_gate_c1_selected_hard_unit_missing_translation():
    art = _c1_artifact()
    del art["learning_package"]["translations_by_paragraph_id"]["u03"]
    assert _gate(_run(_c1_case(), art), "translation_coverage_policy")["passed"] is False


# ---------------------------------------------------------------------------
# judge — message building + fail-closed parsing
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rubric() -> dict:
    return yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))


def _judge_output(rubric: dict, score: int = 4) -> dict:
    return {"dimensions": {d["id"]: {"score": score, "rationale": "依据充分。"}
                           for d in rubric["judge"]["dimensions"]}}


def test_rubric_has_exactly_8_dimensions_with_contract_ids(rubric):
    ids = [d["id"] for d in rubric["judge"]["dimensions"]]
    assert ids == ["source_fidelity", "pedagogical_focus", "difficulty_fit",
                   "article_type_fit", "evidence_retrieval", "transfer_value",
                   "chinese_quality", "learning_sequence"]
    assert all(d["score_min"] == 1 and d["score_max"] == 5 and d["pass_score"] == 4
               for d in rubric["judge"]["dimensions"])
    assert rubric["judge"]["temperature"] == 0.0


def test_judge_messages_contain_full_text_and_gold(rubric):
    case = _b1_case()
    art = _b1_artifact()
    msgs = j2.build_judge_messages_v2(rubric, case, art)
    user = msgs[1]["content"]
    assert case["input"]["original_text"] in user  # untruncated
    assert "小镇在周六开设了一家新图书馆" in user  # full teaching package
    assert "图书馆周日开放" in user  # forbidden facts included
    assert "B1" in user and "news_report" in user
    assert j2.SEMANTIC_NOT_RUN == "SEMANTIC_NOT_RUN"


def test_parse_judge_output_ok(rubric):
    res = j2.parse_judge_output(rubric, json.dumps(_judge_output(rubric, 5)))
    assert res["status"] == "ok"
    assert len(res["dimensions"]) == 8


def test_parse_judge_output_missing_dimension_fails(rubric):
    out = _judge_output(rubric)
    del out["dimensions"]["chinese_quality"]
    res = j2.parse_judge_output(rubric, json.dumps(out))
    assert res["status"] == "error"


def test_parse_judge_output_extra_dimension_fails(rubric):
    out = _judge_output(rubric)
    out["dimensions"]["bonus_dim"] = {"score": 5, "rationale": "x"}
    assert j2.parse_judge_output(rubric, json.dumps(out))["status"] == "error"


@pytest.mark.parametrize("bad_score", [0, 6, 4.0, "4", None, True])
def test_parse_judge_output_illegal_scores_fail(rubric, bad_score):
    out = _judge_output(rubric)
    out["dimensions"]["source_fidelity"]["score"] = bad_score
    assert j2.parse_judge_output(rubric, json.dumps(out))["status"] == "error"


def test_parse_judge_output_empty_rationale_fails(rubric):
    out = _judge_output(rubric)
    out["dimensions"]["transfer_value"]["rationale"] = "   "
    assert j2.parse_judge_output(rubric, json.dumps(out))["status"] == "error"


def test_parse_judge_output_bad_json_fails(rubric):
    assert j2.parse_judge_output(rubric, "not json at all")["status"] == "error"
    assert j2.parse_judge_output(rubric, "")["status"] == "error"


def test_judge_mean_only_on_ok(rubric):
    assert j2.judge_mean_v2({"status": "error"}) is None
    res = j2.parse_judge_output(rubric, json.dumps(_judge_output(rubric, 4)))
    assert j2.judge_mean_v2(res) == 4.0


# ---------------------------------------------------------------------------
# review — acceptance pure functions
# ---------------------------------------------------------------------------


def _review_items(decisions: list[str], factual_errors: int = 0) -> list[dict]:
    items = []
    for i, d in enumerate(decisions):
        items.append({"item_id": f"t{i}", "kind": "checkpoint", "decision": d,
                      "reviewer": "pm", "reviewed_at": "2026-08-21",
                      "factual_major_error": i < factual_errors,
                      "reason": "r", "suggested_edit": None})
    return items


def test_review_acceptance_thresholds():
    ok = rv.evaluate_review(_review_items(["keep"] * 17 + ["minor_edit"] * 3))
    assert ok["accepted"] is True
    bad_ratio = rv.evaluate_review(_review_items(["keep"] * 16 + ["major_edit"] * 4))
    assert bad_ratio["accepted"] is False  # keep+minor 80% < 85%
    factual = rv.evaluate_review(_review_items(["keep"] * 20, factual_errors=1))
    assert factual["accepted"] is False
    heavy = rv.evaluate_review(_review_items(["keep"] * 18 + ["delete"] * 2))
    assert heavy["accepted"] is True
    heavy2 = rv.evaluate_review(_review_items(["keep"] * 17 + ["delete"] * 3))
    assert heavy2["accepted"] is True  # exactly 15% is allowed (不超过 15%)
    heavy3 = rv.evaluate_review(_review_items(["keep"] * 16 + ["delete"] * 4))
    assert heavy3["accepted"] is False  # 20% > 15%


def test_review_pending_and_validation():
    case, art = _b1_case(), _b1_artifact()
    assert rv.review_status(case, art, None)["status"] == rv.HUMAN_REVIEW_PENDING
    pending = {"case_id": "fx-b1", "status": rv.HUMAN_REVIEW_PENDING, "items": []}
    assert rv.review_status(case, art, pending)["status"] == rv.HUMAN_REVIEW_PENDING
    reviewed = {"case_id": "fx-b1", "status": "reviewed",
                "items": _review_items(["keep"] * 10)}
    st = rv.review_status(case, art, reviewed)
    assert st["status"] == "REVIEWED" and st["accepted"] is True


def test_review_strata_independent_no_averaging():
    strata = {"B1": {"accepted": True}, "C1": {"accepted": False}}
    assert rv.strata_all_accepted(strata) is False


# ---------------------------------------------------------------------------
# report — cost block passthrough lock
# ---------------------------------------------------------------------------


def test_cost_block_placeholder_when_usage_missing():
    block = rp.cost_block(None)
    assert block == {
        "status": "NOT_RUN_OWNER_REQUIRED",
        "provider_requests": None,
        "logical_llm_calls": None,
        "retry_count": None,
        "output_retry_count": None,
        "refinement_count": None,
        "per_agent_tokens": None,
        "per_agent_latency_ms": None,
        "end_to_end_latency_ms": None,
        "accepted_teaching_points": None,
        "keep_points_per_1000_output_tokens": None,
    }


def test_cost_block_passthrough_not_overwritten_to_null():
    usage = {
        "provider_requests": 5, "logical_llm_calls": 5, "retry_count": 0,
        "output_retry_count": 1, "refinement_count": 1,
        "per_agent_tokens": {"blueprint": {"input": 100, "output": 90}},
        "per_agent_latency_ms": {"blueprint": 1200},
        "end_to_end_latency_ms": 20000,
        "accepted_teaching_points": 11,
        "keep_points_per_1000_output_tokens": 2.1,
    }
    block = rp.cost_block(usage)
    assert block["status"] != "NOT_RUN_OWNER_REQUIRED"
    for k, v in usage.items():
        assert block[k] == v, f"measured field {k} must pass through unchanged"


# ---------------------------------------------------------------------------
# runner — verdict logic via the score_case helper
# ---------------------------------------------------------------------------


def _runner():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_daily_reader_teaching_eval",
        EVALS_ROOT / "scripts" / "run_daily_reader_teaching_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runner_no_judge_verdict_is_semantic_not_run(rubric):
    mod = _runner()
    res = mod.score_case(rubric, _b1_case(), _b1_artifact(),
                         review_doc=None, skip_judge=True)
    assert res["verdict"] == j2.SEMANTIC_NOT_RUN
    assert res["judge"]["status"] == j2.SEMANTIC_NOT_RUN


def test_runner_human_pending_verdict(rubric):
    mod = _runner()
    judged = {"status": "ok", "dimensions": {
        d["id"]: {"score": 5, "rationale": "ok"} for d in rubric["judge"]["dimensions"]}}
    res = mod.score_case(rubric, _b1_case(), _b1_artifact(),
                         review_doc=None, judge_result=judged)
    assert res["verdict"] == rv.HUMAN_REVIEW_PENDING


def test_runner_pass_requires_all_gates_and_scores(rubric):
    mod = _runner()
    reviewed = {"case_id": "fx-b1", "status": "reviewed",
                "items": _review_items(["keep"] * 10)}
    judged_ok = {"status": "ok", "dimensions": {
        d["id"]: {"score": 5, "rationale": "ok"} for d in rubric["judge"]["dimensions"]}}
    res = mod.score_case(rubric, _b1_case(), _b1_artifact(),
                         review_doc=reviewed, judge_result=judged_ok)
    assert res["verdict"] == "PASS"
    assert res["overall"] >= 0.90
    judged_low = copy.deepcopy(judged_ok)
    judged_low["dimensions"]["chinese_quality"]["score"] = 3
    res2 = mod.score_case(rubric, _b1_case(), _b1_artifact(),
                          review_doc=reviewed, judge_result=judged_low)
    assert res2["verdict"] != "PASS"
    art = _b1_artifact()
    art["run_meta"]["refinement_count"] = 2
    res3 = mod.score_case(rubric, _b1_case(), art,
                          review_doc=reviewed, judge_result=judged_ok)
    assert res3["verdict"] == "FAIL"


def test_runner_reject_case_verdict(rubric):
    mod = _runner()
    res = mod.score_case(rubric, _reject_case(), _reject_artifact(),
                         review_doc=None, skip_judge=True)
    assert res["verdict"] == "PASS"
    assert res["judge"]["status"] == "not_applicable_rejected"


def test_runner_usage_passthrough_in_run(rubric):
    mod = _runner()
    usage_art = json.loads((FIXTURE_DIR / "artifacts" / "fx-b1.with-usage.json")
                           .read_text(encoding="utf-8"))
    res = mod.score_case(rubric, _b1_case(), usage_art,
                         review_doc=None, skip_judge=True)
    assert res["cost"]["provider_requests"] == 5
    assert res["cost"]["status"] != "NOT_RUN_OWNER_REQUIRED"


# ---------------------------------------------------------------------------
# dataset self-consistency (frozen real articles + gold sanity)
# ---------------------------------------------------------------------------


def test_all_dataset_cases_validate_and_golds_are_draft():
    cases = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted((DATASET_DIR / "cases").glob("*.json"))]
    assert 8 <= len(cases) <= 12
    for case in cases:
        assert sc.validate_case(case) == [], case["case_id"]
        assert case["gold"]["annotation_status"] == "DRAFT_PM_REVIEW"
        assert case["origin"]["frozen_real_article"] is True
        assert case["origin"]["source_url"]
        assert case["origin"]["captured_at"]


def test_every_dataset_case_has_pending_or_na_review():
    for p in sorted((DATASET_DIR / "cases").glob("*.json")):
        review_path = DATASET_DIR / "reviews" / p.name
        assert review_path.exists(), f"missing review placeholder for {p.name}"
        doc = json.loads(review_path.read_text(encoding="utf-8"))
        assert doc["status"] in {rv.HUMAN_REVIEW_PENDING, "not_applicable_reject"}


def test_green_fixtures_pass_all_gates_on_real_dataset_cases():
    """Committed reference artifacts must be all-green against the dataset."""
    for case_path in sorted((DATASET_DIR / "cases").glob("*.json")):
        case = json.loads(case_path.read_text(encoding="utf-8"))
        art_path = FIXTURE_DIR / "artifacts" / f"{case['case_id']}.artifact.json"
        assert art_path.exists(), f"missing reference artifact for {case['case_id']}"
        art = json.loads(art_path.read_text(encoding="utf-8"))
        res = _run(case, art)
        failing = {k: v for k, v in res["gates"].items() if v["passed"] is False}
        assert failing == {}, f"{case['case_id']}: {failing}"
