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


def test_checkpoint_subject_meaning_is_deferred_to_semantic_review() -> None:
    blueprint, package = _valid_contract()
    package["comprehension_checkpoints"][0]["prompt_subject"] = "Trump family"
    package["comprehension_checkpoints"][0]["reference_answer_subject"] = "Sun"
    assert "checkpoint_subject_metadata_invalid" not in _issue_codes(blueprint, package)


def test_c1_sentence_map_requires_declared_complexity_metadata() -> None:
    blueprint, package = _valid_contract()
    blueprint["effective_difficulty"] = "C1"
    package["sentence_maps"][0]["complexity_kind"] = "simple_sentence"
    assert "c1_sentence_map_complexity_metadata_invalid" in _issue_codes(blueprint, package)


def test_full_sentence_language_target_duplicate_of_sentence_map_is_reported() -> None:
    blueprint, package = _valid_contract()
    sentence_map = package["sentence_maps"][0]
    package["language_targets"][0].update(
        expression=sentence_map["sentence"],
        teaching_purpose=sentence_map["teaching_purpose"],
    )
    assert "duplicate_language_target_sentence_map" in _issue_codes(blueprint, package)


def test_reading_mission_text_is_not_classified_by_deterministic_validator() -> None:
    blueprint, package = _valid_contract()
    blueprint["reading_mission"] = "Find evidence to support the author's position."
    assert "reading_mission_stance_metadata_invalid" not in _issue_codes(blueprint, package)

    blueprint["reading_mission_stance"] = "support_author"
    assert "reading_mission_stance_metadata_invalid" in _issue_codes(blueprint, package)


def test_language_target_value_is_deferred_to_semantic_review() -> None:
    blueprint, package = _valid_contract()
    package["language_targets"][0]["target_kind"] = "fact_sentence"
    assert "language_target_metadata_invalid" not in _issue_codes(blueprint, package)


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


def test_deterministic_validator_fails_closed_on_wrong_collection_types() -> None:
    blueprint, package = _valid_contract()
    package["language_targets"] = {"not": "a list"}
    package["sentence_maps"] = "not a list"
    package["comprehension_checkpoints"] = None
    assert {"language_target_count", "sentence_map_count", "checkpoint_count"} <= _issue_codes(
        blueprint, package
    )


def test_dense_per_paragraph_or_whole_article_fields_are_rejected() -> None:
    blueprint, package = _valid_contract()
    package.update(
        focus_questions=["one per paragraph"],
        micro_summaries=["one per paragraph"],
        full_translation="whole article translation",
    )
    issues = validate_teaching_contract(blueprint, package)
    assert [issue["code"] for issue in issues].count("dense_teaching_field") == 3


def test_transfer_content_fit_is_deferred_to_semantic_review() -> None:
    blueprint, package = _valid_contract()
    blueprint["article_type"] = "opinion_commentary"
    package["transfer_task"].update(
        task_kind="counter",
        content_requirement="fact_chain",
    )
    assert "transfer_content_metadata_invalid" not in _issue_codes(blueprint, package)

    package["transfer_task"]["content_requirement"] = "social_post"
    assert "transfer_content_metadata_invalid" in _issue_codes(blueprint, package)


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
    assert all(contract in prompts["semantic_review"] for contract in SEMANTIC_REVIEW_CONTRACTS)
    assert all(contract in prompts["refinement"] for contract in SEMANTIC_REVIEW_CONTRACTS)
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
        contract_results=_contract_results(failing_contract="transfer_mapping"),
        reviewed_at_stage="before_refinement",
        refinement_requested=True,
    )
    assert set(evidence) == {
        "verdict",
        "issues",
        "remaining_issues",
        "contract_results",
        "checked_contracts",
        "reviewed_at_stage",
        "refinement_requested",
    }
    assert evidence["checked_contracts"] == list(SEMANTIC_REVIEW_CONTRACTS)
    assert [result["contract"] for result in evidence["contract_results"]] == list(
        SEMANTIC_REVIEW_CONTRACTS
    )
    assert json.loads(json.dumps(evidence)) == evidence


def _before_and_after_review() -> tuple[dict, dict]:
    before = make_review_evidence(
        verdict="FAIL",
        issues=[{"field": "transfer_task", "problem": "wrong direction"}],
        remaining_issues=["transfer direction"],
        contract_results=_contract_results(failing_contract="transfer_mapping"),
        reviewed_at_stage="before_refinement",
        refinement_requested=True,
    )
    after = make_review_evidence(
        verdict="PASS",
        issues=[],
        remaining_issues=[],
        contract_results=_contract_results(),
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
            contract_results=_contract_results(),
            reviewed_at_stage="before_refinement",
            refinement_requested=False,
        )
    )
    responses = _minimal_stage_responses() + [review]
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


def _contract_results(*, failing_contract: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "contract": contract,
            "passed": contract != failing_contract,
            "rationale": f"Substantive check for {contract}.",
        }
        for contract in SEMANTIC_REVIEW_CONTRACTS
    ]


def _review_payload(verdict: str, stage: str) -> dict[str, Any]:
    failing_contract = SEMANTIC_REVIEW_CONTRACTS[0] if verdict == "FAIL" else None
    return {
        "verdict": verdict,
        "issues": (
            [] if verdict == "PASS" else [{"field": "transfer_task", "problem": "wrong direction"}]
        ),
        "remaining_issues": [] if verdict == "PASS" else ["transfer direction"],
        "contract_results": _contract_results(failing_contract=failing_contract),
        "reviewed_at_stage": stage,
        "refinement_requested": verdict == "FAIL" and stage == "before_refinement",
    }


def test_pass_review_rejects_nonempty_issues() -> None:
    payload = _review_payload("PASS", "before_refinement")
    payload["issues"] = [{"field": "transfer_task", "problem": "contradiction"}]
    with pytest.raises(ValueError, match="PASS review requires empty issues"):
        make_review_evidence(**payload)


def test_pass_review_rejects_nonempty_remaining_issues() -> None:
    payload = _review_payload("PASS", "before_refinement")
    payload["remaining_issues"] = ["unresolved contradiction"]
    with pytest.raises(ValueError, match="PASS review requires empty remaining_issues"):
        make_review_evidence(**payload)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra"])
def test_review_rejects_inexact_semantic_contract_results(mutation: str) -> None:
    payload = _review_payload("PASS", "before_refinement")
    results = payload["contract_results"]
    if mutation == "missing":
        results.pop()
    elif mutation == "duplicate":
        results[-1] = dict(results[0])
    else:
        results.append({"contract": "invented_contract", "passed": True, "rationale": "Invented."})
    with pytest.raises(ValueError, match="each semantic contract exactly once"):
        make_review_evidence(**payload)


@pytest.mark.parametrize(
    "broken_result",
    [
        {"contract": SEMANTIC_REVIEW_CONTRACTS[0], "rationale": "Checked."},
        {"contract": SEMANTIC_REVIEW_CONTRACTS[0], "passed": 1, "rationale": "Checked."},
        {"contract": SEMANTIC_REVIEW_CONTRACTS[0], "passed": True, "rationale": "   "},
    ],
)
def test_contract_result_requires_strict_bool_and_nonempty_rationale(
    broken_result: dict[str, Any],
) -> None:
    payload = _review_payload("PASS", "before_refinement")
    payload["contract_results"][0] = broken_result
    with pytest.raises(ValueError, match="passed bool and non-empty rationale"):
        make_review_evidence(**payload)


def test_fail_review_requires_at_least_one_failed_contract_result() -> None:
    payload = _review_payload("FAIL", "before_refinement")
    payload["contract_results"] = _contract_results()
    with pytest.raises(ValueError, match="FAIL review requires a failed contract result"):
        make_review_evidence(**payload)


def test_pass_review_requires_every_contract_result_to_pass() -> None:
    payload = _review_payload("PASS", "before_refinement")
    payload["contract_results"] = _contract_results(failing_contract="source_fidelity")
    with pytest.raises(ValueError, match="every contract result to pass"):
        make_review_evidence(**payload)


def test_fail_review_requires_remaining_issues() -> None:
    payload = _review_payload("FAIL", "before_refinement")
    payload["remaining_issues"] = []
    with pytest.raises(ValueError, match="FAIL review requires remaining_issues"):
        make_review_evidence(**payload)


def test_checked_contracts_cannot_override_contract_results() -> None:
    payload = _review_payload("PASS", "before_refinement")
    payload["checked_contracts"] = list(reversed(SEMANTIC_REVIEW_CONTRACTS))
    with pytest.raises(ValueError, match="derived from contract_results"):
        make_review_evidence(**payload)


def test_refinement_rejects_after_review_without_complete_contract_evidence() -> None:
    before = _review_payload("FAIL", "before_refinement")
    after = _review_payload("PASS", "after_refinement")
    after.pop("contract_results")
    with pytest.raises(ValueError, match="contract_results"):
        build_refinement_evidence(
            review_before_refinement=before,
            refinement_patch={"transfer_task": {"task_kind": "retell"}},
            review_after_refinement=after,
            hard_gate_replay={"all_passed": True},
            prior_refinement_count=0,
        )


def test_refinement_rejects_pass_after_review_when_hard_gates_fail() -> None:
    with pytest.raises(ValueError, match="PASS after-review requires hard gates to pass"):
        build_refinement_evidence(
            review_before_refinement=_review_payload("FAIL", "before_refinement"),
            refinement_patch={"transfer_task": {"task_kind": "retell"}},
            review_after_refinement=_review_payload("PASS", "after_refinement"),
            hard_gate_replay={"all_passed": False},
            prior_refinement_count=0,
        )


@pytest.mark.parametrize("not_bool", [1, 0, "true"])
def test_refinement_requires_strict_bool_hard_gate_result(not_bool: Any) -> None:
    with pytest.raises(ValueError, match="hard-gate replay must record all_passed"):
        build_refinement_evidence(
            review_before_refinement=_review_payload("FAIL", "before_refinement"),
            refinement_patch={"transfer_task": {"task_kind": "retell"}},
            review_after_refinement=_review_payload("PASS", "after_refinement"),
            hard_gate_replay={"all_passed": not_bool},
            prior_refinement_count=0,
        )


def _minimal_stage_responses() -> list[dict[str, Any]]:
    return [
        {
            "article_type": "news_report",
            "effective_difficulty": "B1",
            "reading_mission": "Trace the reported evidence.",
        },
        {
            "language_targets": [{"expression": "by contrast", "paragraph_id": "u02"}],
            "sentence_maps": [{"paragraph_id": "u03", "complexity_kind": "complex_syntax"}],
            "high_difficulty_unit_ids": ["u03"],
        },
        {"translations": [{"paragraph_id": "u03", "translation": "合成译文。"}]},
    ]


@pytest.mark.parametrize(
    ("empty_stage", "empty_index"),
    [("blueprint", 0), ("language_support", 1), ("translation", 2)],
)
def test_dry_run_rejects_empty_generation_stage_objects(empty_stage: str, empty_index: int) -> None:
    responses = _minimal_stage_responses()
    responses[empty_index] = {}
    responses.append(_review_payload("PASS", "before_refinement"))
    invoke, _, _ = _function_model_invoker(responses)
    with pytest.raises(ValueError, match=rf"{empty_stage} response"):
        run_prototype_dry_run(invoke, _generation_prompts())
