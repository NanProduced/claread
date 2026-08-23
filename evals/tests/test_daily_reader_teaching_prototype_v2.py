import json
from collections.abc import Callable
from typing import Any

import pytest

from claread_eval.daily_reader.teaching_v2.prototype import (
    SEMANTIC_REVIEW_CONTRACTS,
    build_blueprint_prompt,
    build_language_support_prompt,
    build_refinement_evidence,
    build_refinement_prompt,
    build_semantic_review_prompt,
    build_translation_prompt,
    derive_translation_unit_ids,
    make_review_evidence,
    run_prototype_dry_run,
    transfer_task_kind,
    validate_teaching_contract,
)

UNITS = [
    {"id": "u01", "text": "Pure source chrome."},
    {"id": "u02", "text": "First substantive unit."},
    {"id": "u03", "text": "Second substantive unit."},
    {"id": "u04", "text": "Third substantive unit."},
]


def _valid_contract() -> tuple[dict, dict]:
    blueprint = {
        "article_type": "news_report",
        "effective_difficulty": "B1",
        "reading_mission": "Read the evidence and decide what it supports.",
        "reading_mission_stance": "neutral",
    }
    package = {
        "language_targets": [
            {
                "expression": "run its course",
                "paragraph_id": "u02",
                "target_kind": "idiom",
                "teaching_purpose": "transferable usage",
            },
            {
                "expression": "make the first move",
                "paragraph_id": "u03",
                "target_kind": "phrase",
                "teaching_purpose": "transferable usage",
            },
            {
                "expression": "by contrast",
                "paragraph_id": "u04",
                "target_kind": "discourse_link",
                "teaching_purpose": "cohesion",
            },
        ],
        "sentence_maps": [
            {
                "sentence": "Although demand fell, the company stayed open because exports grew.",
                "paragraph_id": "u04",
                "complexity_kind": "complex_syntax",
                "teaching_purpose": "clause relations",
            }
        ],
        "comprehension_checkpoints": [
            {
                "prompt": "What did the company do?",
                "prompt_subject": "the company",
                "reference_answer": "The company stayed open.",
                "reference_answer_subject": "the company",
            },
            {
                "prompt": "Why did exports matter?",
                "prompt_subject": "exports",
                "reference_answer": "Exports offset weaker demand.",
                "reference_answer_subject": "exports",
            },
        ],
        "transfer_task": {
            "task_kind": "retell",
            "required_language_target_expressions": ["run its course"],
            "content_requirement": "fact_chain",
        },
        "high_difficulty_unit_ids": ["u04"],
    }
    return blueprint, package


def _issue_codes(blueprint: dict, package: dict) -> set[str]:
    return {issue["code"] for issue in validate_teaching_contract(blueprint, package)}


@pytest.mark.parametrize(
    ("article_type", "expected"),
    [
        ("news_report", "retell"),
        ("opinion_commentary", "counter"),
        ("explainer", "explain"),
        ("narrative_profile", "rewrite"),
    ],
)
def test_article_type_has_one_transfer_task_kind(article_type: str, expected: str) -> None:
    assert transfer_task_kind(article_type) == expected


def test_b1_translates_every_substantive_unit_but_not_pure_dirty_unit() -> None:
    assert derive_translation_unit_ids(
        "B1",
        UNITS,
        substantive_unit_ids=["u02", "u03", "u04"],
    ) == ["u02", "u03", "u04"]


def test_b2_translates_anchor_union_in_source_order() -> None:
    assert derive_translation_unit_ids(
        "B2",
        UNITS,
        substantive_unit_ids=["u02", "u03", "u04"],
        checkpoint_evidence_ids=["u04", "u02"],
        language_target_paragraph_ids=["u03", "u04"],
        sentence_map_paragraph_ids=["u03"],
    ) == ["u02", "u03", "u04"]


def test_c1_translates_only_high_difficulty_and_sentence_map_units() -> None:
    assert derive_translation_unit_ids(
        "C1",
        UNITS,
        substantive_unit_ids=["u02", "u03", "u04"],
        checkpoint_evidence_ids=["u02"],
        language_target_paragraph_ids=["u02"],
        sentence_map_paragraph_ids=["u03"],
        high_difficulty_unit_ids=["u04"],
    ) == ["u03", "u04"]


def test_c1_does_not_translate_an_ordinary_teaching_anchor() -> None:
    assert derive_translation_unit_ids(
        "C1",
        UNITS,
        substantive_unit_ids=["u02", "u03", "u04"],
        checkpoint_evidence_ids=["u02"],
        language_target_paragraph_ids=["u03"],
        high_difficulty_unit_ids=["u04"],
    ) == ["u04"]


def test_translation_selection_fails_closed_on_unknown_paragraph_id() -> None:
    with pytest.raises(ValueError, match="non-substantive paragraph id"):
        derive_translation_unit_ids(
            "B2",
            UNITS,
            substantive_unit_ids=["u02", "u03", "u04"],
            checkpoint_evidence_ids=["u99"],
        )


def test_transfer_must_use_an_already_taught_expression() -> None:
    blueprint, package = _valid_contract()
    package["transfer_task"]["required_language_target_expressions"] = ["never taught"]
    assert "transfer_expression_not_taught" in _issue_codes(blueprint, package)


@pytest.mark.parametrize(
    ("article_type", "wrong_kind"),
    [
        ("news_report", "counter"),
        ("opinion_commentary", "rewrite"),
        ("explainer", "counter"),
        ("narrative_profile", "retell"),
    ],
)
def test_wrong_transfer_mapping_is_rejected(article_type: str, wrong_kind: str) -> None:
    blueprint, package = _valid_contract()
    blueprint["article_type"] = article_type
    package["transfer_task"]["task_kind"] = wrong_kind
    assert "transfer_task_kind_mismatch" in _issue_codes(blueprint, package)


def test_checkpoint_prompt_and_answer_subject_conflict_is_reported() -> None:
    blueprint, package = _valid_contract()
    package["comprehension_checkpoints"][0]["prompt_subject"] = "Trump family"
    package["comprehension_checkpoints"][0]["reference_answer_subject"] = "Sun"
    assert "checkpoint_subject_conflict" in _issue_codes(blueprint, package)


def test_c1_simple_sentence_map_is_rejected() -> None:
    blueprint, package = _valid_contract()
    blueprint["effective_difficulty"] = "C1"
    package["sentence_maps"][0]["complexity_kind"] = "simple_sentence"
    assert "c1_sentence_map_not_complex" in _issue_codes(blueprint, package)


def test_full_sentence_language_target_duplicate_of_sentence_map_is_reported() -> None:
    blueprint, package = _valid_contract()
    sentence_map = package["sentence_maps"][0]
    package["language_targets"][0].update(
        expression=sentence_map["sentence"],
        teaching_purpose=sentence_map["teaching_purpose"],
    )
    assert "duplicate_language_target_sentence_map" in _issue_codes(blueprint, package)


def test_one_sided_reading_mission_is_reported() -> None:
    blueprint, package = _valid_contract()
    blueprint["reading_mission"] = "Find evidence to support the author's position."
    blueprint["reading_mission_stance"] = "support_author"
    assert "reading_mission_not_neutral" in _issue_codes(blueprint, package)


def test_plain_fact_sentence_is_not_a_language_target() -> None:
    blueprint, package = _valid_contract()
    package["language_targets"][0]["target_kind"] = "fact_sentence"
    assert "low_value_language_target" in _issue_codes(blueprint, package)


def test_teaching_point_counts_and_single_transfer_task_are_enforced() -> None:
    blueprint, package = _valid_contract()
    package["language_targets"] = package["language_targets"][:2]
    package["sentence_maps"] = []
    package["comprehension_checkpoints"] = package["comprehension_checkpoints"][:1]
    package["transfer_task"] = []
    assert {
        "language_target_count",
        "sentence_map_count",
        "checkpoint_count",
        "transfer_task_count",
    } <= _issue_codes(blueprint, package)


def test_dense_per_paragraph_or_whole_article_fields_are_rejected() -> None:
    blueprint, package = _valid_contract()
    package.update(
        focus_questions=["one per paragraph"],
        micro_summaries=["one per paragraph"],
        full_translation="whole article translation",
    )
    issues = validate_teaching_contract(blueprint, package)
    assert [issue["code"] for issue in issues].count("dense_teaching_field") == 3


@pytest.mark.parametrize(
    ("article_type", "task_kind", "wrong_content"),
    [
        ("news_report", "retell", "social_post"),
        ("opinion_commentary", "counter", "fact_chain"),
        ("explainer", "explain", "original_stance"),
        ("narrative_profile", "rewrite", "conclusion_only"),
    ],
)
def test_transfer_content_must_fit_article_type(
    article_type: str, task_kind: str, wrong_content: str
) -> None:
    blueprint, package = _valid_contract()
    blueprint["article_type"] = article_type
    package["transfer_task"].update(
        task_kind=task_kind,
        content_requirement=wrong_content,
    )
    assert "transfer_content_mismatch" in _issue_codes(blueprint, package)


def _generation_prompts() -> dict[str, str]:
    blueprint, package = _valid_contract()
    article = {"title": "Synthetic", "source": "offline", "reading_units": UNITS[1:]}
    return {
        "blueprint": build_blueprint_prompt(article),
        "language_support": build_language_support_prompt([UNITS[1]], "B2"),
        "translation": build_translation_prompt([UNITS[2]], package["sentence_maps"], "B2"),
        "semantic_review": build_semantic_review_prompt(
            "Synthetic article body.", blueprint, package, {"all_passed": True}
        ),
        "refinement": build_refinement_prompt(
            [{"field": "transfer_task", "problem": "wrong direction"}],
            {"transfer_task": package["transfer_task"]},
        ),
    }


def test_generation_prompts_have_no_evaluation_answer_keys() -> None:
    prompts = _generation_prompts()
    forbidden = {
        "gold",
        "expected_difficulty",
        "expected_article_type",
        "allowed_paragraph_ids",
        "required_paragraph_ids",
    }
    combined = "\n".join(prompts.values()).casefold()
    assert not forbidden.intersection(combined.split())
    assert all(value not in combined for value in forbidden)
    with pytest.raises(ValueError, match="forbidden generation key"):
        build_blueprint_prompt(
            {
                "title": "leak",
                "reading_units": UNITS,
                "expected_difficulty": "B1",
            }
        )


def test_flash_and_refinement_prompts_do_not_receive_unrelated_full_text() -> None:
    prompts = _generation_prompts()
    assert UNITS[0]["text"] not in prompts["language_support"]
    assert UNITS[0]["text"] not in prompts["translation"]
    assert UNITS[0]["text"] not in prompts["refinement"]


def test_semantic_review_evidence_is_complete_and_json_serializable() -> None:
    evidence = make_review_evidence(
        verdict="FAIL",
        issues=[{"field": "transfer_task", "problem": "wrong direction"}],
        remaining_issues=["transfer direction"],
        checked_contracts=list(SEMANTIC_REVIEW_CONTRACTS),
        reviewed_at_stage="before_refinement",
        refinement_requested=True,
    )
    assert set(evidence) == {
        "verdict",
        "issues",
        "remaining_issues",
        "checked_contracts",
        "reviewed_at_stage",
        "refinement_requested",
    }
    assert json.loads(json.dumps(evidence)) == evidence


def _before_and_after_review() -> tuple[dict, dict]:
    before = make_review_evidence(
        verdict="FAIL",
        issues=[{"field": "transfer_task", "problem": "wrong direction"}],
        remaining_issues=["transfer direction"],
        checked_contracts=list(SEMANTIC_REVIEW_CONTRACTS),
        reviewed_at_stage="before_refinement",
        refinement_requested=True,
    )
    after = make_review_evidence(
        verdict="PASS",
        issues=[],
        remaining_issues=[],
        checked_contracts=list(SEMANTIC_REVIEW_CONTRACTS),
        reviewed_at_stage="after_refinement",
        refinement_requested=False,
    )
    return before, after


def test_refinement_preserves_before_patch_after_and_hard_gate_replay() -> None:
    before, after = _before_and_after_review()
    evidence = build_refinement_evidence(
        review_before_refinement=before,
        refinement_patch={"transfer_task": {"task_kind": "retell"}},
        review_after_refinement=after,
        hard_gate_replay={"all_passed": True, "issues": []},
        prior_refinement_count=0,
    )
    assert evidence["review_before_refinement"] == before
    assert evidence["refinement_patch"] == {"transfer_task": {"task_kind": "retell"}}
    assert evidence["review_after_refinement"] == after
    assert evidence["hard_gate_replay"]["all_passed"] is True
    assert evidence["refinement_count"] == 1
    assert json.loads(json.dumps(evidence)) == evidence


def test_second_refinement_attempt_fails_closed() -> None:
    before, after = _before_and_after_review()
    with pytest.raises(RuntimeError, match="second refinement"):
        build_refinement_evidence(
            review_before_refinement=before,
            refinement_patch={"transfer_task": {"task_kind": "retell"}},
            review_after_refinement=after,
            hard_gate_replay={"all_passed": True},
            prior_refinement_count=1,
        )


def _function_model_invoker(
    responses: list[dict[str, Any]],
) -> tuple[Callable[[str, str], dict[str, Any]], list[str], list[dict[str, Any]]]:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    messages_module = pytest.importorskip("pydantic_ai.messages")
    function_module = pytest.importorskip("pydantic_ai.models.function")
    agent_type = pydantic_ai.Agent
    model_response_type = messages_module.ModelResponse
    text_part_type = messages_module.TextPart
    function_model_type = function_module.FunctionModel
    queued = list(responses)
    model_calls: list[str] = []

    def handler(messages: list[Any], info: Any) -> Any:
        model_calls.append("request")
        return model_response_type(parts=[text_part_type(json.dumps(queued.pop(0)))])

    agent = agent_type(function_model_type(handler))

    def invoke(stage: str, prompt: str) -> dict[str, Any]:
        return json.loads(agent.run_sync(f"{stage}\n{prompt}").output)

    return invoke, model_calls, queued


def _topology_responses(review_verdict: str) -> list[dict]:
    before, after = _before_and_after_review()
    review = (
        before
        if review_verdict == "FAIL"
        else make_review_evidence(
            verdict="PASS",
            issues=[],
            remaining_issues=[],
            checked_contracts=list(SEMANTIC_REVIEW_CONTRACTS),
            reviewed_at_stage="before_refinement",
            refinement_requested=False,
        )
    )
    responses = [{}, {}, {}, review]
    if review_verdict == "FAIL":
        responses.append(
            {
                "refinement_patch": {"transfer_task": {"task_kind": "retell"}},
                "review_after_refinement": after,
                "hard_gate_replay": {"all_passed": True, "issues": []},
            }
        )
    return responses


def test_function_model_normal_dry_run_has_exactly_four_logical_calls() -> None:
    prompts = _generation_prompts()
    invoke, model_calls, _ = _function_model_invoker(_topology_responses("PASS"))
    result = run_prototype_dry_run(invoke, prompts)
    assert result["logical_call_count"] == 4
    assert len(model_calls) == 4
    assert result["prompt_chars"] == {
        stage: len(prompts[stage])
        for stage in ("blueprint", "language_support", "translation", "semantic_review")
    }


def test_function_model_failed_review_dry_run_has_exactly_five_logical_calls() -> None:
    prompts = _generation_prompts()
    invoke, model_calls, _ = _function_model_invoker(_topology_responses("FAIL"))
    result = run_prototype_dry_run(invoke, prompts)
    assert result["logical_call_count"] == 5
    assert len(model_calls) == 5
    assert result["calls"][-1]["stage"] == "refinement"


def test_sixth_function_model_call_is_unreachable() -> None:
    responses = _topology_responses("FAIL") + [{"forbidden": "sixth call"}]
    invoke, model_calls, queued = _function_model_invoker(responses)
    result = run_prototype_dry_run(invoke, _generation_prompts())
    assert result["logical_call_count"] == 5
    assert len(model_calls) == 5
    assert queued == [{"forbidden": "sixth call"}]
