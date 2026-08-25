"""Canonical teaching-v2 five-stage prompts and evidence contracts (shared).

Moved verbatim from the evals ``teaching_v2/prototype.py`` (P-5B): the
production workflow and the evals stack now consume ONE implementation, so
the stage prompts, review-evidence contracts and the translation-target
derivation cannot drift. Hard constraint: stdlib only — no pydantic, no
network, no DB — so both virtualenvs can import this package.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.services.daily_reader.teaching.contract import (  # noqa: F401
    TRANSFER_CONTENT_REQUIREMENT_VALUES,
    TRANSFER_TASK_KIND_BY_ARTICLE_TYPE,
    validate_teaching_contract,
)

_FORBIDDEN_GENERATION_KEYS = {
    "gold",
    "expected_difficulty",
    "expected_article_type",
    "allowed_paragraph_ids",
    "required_paragraph_ids",
}

SEMANTIC_REVIEW_CONTRACTS = (
    "source_fidelity",
    "checkpoint_subject_consistency",
    "evidence_anchors",
    "difficulty_fit",
    "translation_selection",
    "transfer_mapping",
    "transfer_language_use",
    "repeated_teaching_points",
    "language_target_value",
    "reading_mission_neutrality",
)


def _assert_generation_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in _FORBIDDEN_GENERATION_KEYS:
                raise ValueError(f"forbidden generation key: {key}")
            _assert_generation_safe(child)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for child in value:
            _assert_generation_safe(child)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_blueprint_prompt(article: Mapping[str, Any]) -> str:
    """Build the single whole-article generation prompt."""
    _assert_generation_safe(article)
    return """You design a sparse Daily Reader lesson from the supplied article.

Decide article_type and effective_difficulty independently. article_type is exactly one of
news_report, opinion_commentary, explainer, narrative_profile. Classify it by the article's
dominant reading task: news_report reports verifiable events, where 5W1H facts, timelines,
source attribution and quotes dominate; opinion_commentary argues, where a thesis, reasons,
evidence, concessions, counterarguments and tone dominate over new facts; explainer explains,
where concepts, mechanisms, causal chains, comparisons and limits dominate; narrative_profile
portrays a person or story arc, where motivations, turns, scenes and voice dominate. Mixed
articles take the single most explanatory type for the main reading task; never invent a fifth
type. Set reading_mission_stance to
neutral: the mission must not prescribe which side the reader should support. Identify core
evidence, candidate transferable language, and genuinely difficult unit ids. Do not generate
translations or detailed language explanations in this stage.
Choose effective_difficulty from the article's actual sentence complexity, vocabulary density
and discourse devices, and be ready to cite that concrete textual evidence for the level you
declare.

Produce 2-4 evidence checkpoints with prompt_subject and reference_answer_subject for audit.
learning_objectives holds exactly 1-2 items and structure_map holds 2-6 nodes. Anchor every
paragraph reference to the exact reading unit ids supplied in ARTICLE.reading_units (ids like
u07); never emit bare numbers. Produce exactly one transfer task using this fixed mapping:
news_report=retell, opinion_commentary=counter, explainer=explain, narrative_profile=rewrite.
Its required_language_target_expressions must name at least one expression for language support
to teach. Its content_requirement must be fact_chain, original_stance, mechanism_or_causality, or
one of character_motivation, scene_contrast, quotation_characterization, narrative_viewpoint,
as appropriate to the article type. A narrative rewrite must use the named narrative technique.

ARTICLE:
""" + _stable_json(article)


def build_language_support_prompt(
    selected_units: Sequence[Mapping[str, Any]], effective_difficulty: str
) -> str:
    """Build the Flash language-support prompt from selected units only."""
    payload = {"effective_difficulty": effective_difficulty, "selected_units": selected_units}
    _assert_generation_safe(payload)
    return """Create language support using only the supplied selected units.

Return 3-5 transferable language targets. Prefer Tier II language, idioms, stance or tone,
rhetoric, and discourse links; reject ordinary complete fact sentences. Quote every target
expression and every sentence-map sentence verbatim from its unit — exact words and inflection;
if an inflected form matters, explain it in usage_note instead of altering the quotation. Use
the exact unit ids from SELECTED INPUT. Each target includes target_kind and teaching_purpose,
and must fill meaning_zh, usage_note, and reusable_pattern with concrete non-empty values
grounded in the unit's context; leaving any of them empty or placeholder is a contract
violation. Return 1-2 sentence maps. For C1, every map must be either complex_syntax or
argument_structure and must explain why. Return explicit high_difficulty_unit_ids. Do not add
fields used by evaluation answers.

SELECTED INPUT:
""" + _stable_json(payload)


def build_translation_prompt(
    target_units: Sequence[Mapping[str, Any]],
    sentence_maps: Sequence[Mapping[str, Any]],
    effective_difficulty: str,
) -> str:
    """Build the Flash translation prompt from deterministic targets only."""
    payload = {
        "effective_difficulty": effective_difficulty,
        "sentence_maps": sentence_maps,
        "target_units": target_units,
    }
    _assert_generation_safe(payload)
    return """Translate exactly the supplied target units and no others.

Return one translation for each supplied unit id. Never invent or add a paragraph id. Write
every translation as natural Simplified Chinese prose suited to the declared learner level;
returning the source text unchanged or nearly unchanged is a contract violation, not a
translation. When a sentence map belongs to a target unit, reuse its single canonical
translation verbatim inside the unit translation.

TARGET INPUT:
""" + _stable_json(payload)


def build_semantic_review_prompt(
    original_text: str,
    blueprint: Mapping[str, Any],
    learning_package: Mapping[str, Any],
    deterministic_checks: Mapping[str, Any],
) -> str:
    """Build the auditable semantic-review prompt."""
    payload = {
        "blueprint": blueprint,
        "deterministic_checks": deterministic_checks,
        "learning_package": learning_package,
        "original_text": original_text,
        "semantic_review_contracts": SEMANTIC_REVIEW_CONTRACTS,
    }
    _assert_generation_safe(payload)
    return """Audit the lesson and persist substantive review evidence.

Check factual fidelity, checkpoint subject consistency, evidence anchors, difficulty fit,
translation selection, transfer mapping and content, repeated teaching points, language-target
value, and mission neutrality. Return one contract_results item for every named contract, each
        with contract, a strict boolean passed, and a substantive non-empty rationale. Return
        verdict, issues, remaining_issues, contract_results, reviewed_at_stage='before_refinement',
        and refinement_requested. PASS requires every result to pass and both issue lists to be
        empty. FAIL additionally requires at least one non-empty string in remaining_issues:
        remaining_issues names what still needs fixing or verifying after refinement, and an empty
        remaining_issues list on FAIL is invalid. Every issue field must address exactly one
        location: use 'learning_package.<field>...' for teaching-package items,
        'blueprint.<field>...' or 'lesson_blueprint.<field>...' for lesson-blueprint items, or a
        direct path inside one of them; never return a bare container name alone. FAIL requires a
        failed result and directed issues.
        Every FAIL issue must contain contract, field, and problem; every failed contract needs an
        issue and no issue may name a passed contract.
        difficulty_fit judges fit in both directions: a lower declared level is not automatically
        wrong, and any issue requesting a level change must cite concrete textual evidence that
        the declared level misfits the article's actual vocabulary and syntax.
        reading_mission_neutrality fails only when the mission text itself prescribes which side
        the reader must take; contested subject matter alone is not a neutrality violation.
checked_contracts, if emitted, is only the ordered list derived from contract_results; never
replace the audit with a bare verdict or name list.

REVIEW INPUT:
""" + _stable_json(payload)


def build_refinement_prompt(
    review_before_refinement: Mapping[str, Any],
    fields_to_fix: Mapping[str, Any],
    evidence_context: Mapping[str, Any],
) -> str:
    """Build the sole directed-refinement prompt without unrelated content."""
    before = _validate_review_evidence(review_before_refinement)
    if before["reviewed_at_stage"] != "before_refinement" or before["verdict"] != "FAIL":
        raise ValueError("refinement prompt requires a failing before-review")
    if (
        not isinstance(fields_to_fix, Mapping)
        or not fields_to_fix
        or any(not isinstance(field, str) or not field.strip() for field in fields_to_fix)
    ):
        raise ValueError("fields_to_fix must be a non-empty object")
    if not isinstance(evidence_context, Mapping):
        raise ValueError("evidence_context must be an object")
    failed_contracts = [
        result["contract"] for result in before["contract_results"] if not result["passed"]
    ]
    payload = {
        "fields_to_fix": fields_to_fix,
        "failed_contracts": failed_contracts,
        "issues": before["issues"],
        "evidence_context": evidence_context,
    }
    _assert_generation_safe(payload)
    return """Perform the one permitted directed refinement.

Modify only fields_to_fix in response to the directed issues and minimal evidence_context. Return
only refinement_patch, rechecked_contract_results, and remaining_issues. The rechecked results
must cover exactly failed_contracts, each with contract, strict boolean passed, and a substantive
non-empty rationale. Each remaining issue must contain contract, field, and problem. Do not review
unaffected contracts or return a complete after-review. No second refinement is permitted.
Every value you return must obey the frozen metadata contracts: reading_mission_stance stays
exactly neutral, and declared enums keep their allowed values. Never introduce empty strings,
placeholder text, or English source text inside translations: translations stay complete natural
Simplified Chinese renderings that preserve every date, number, and proper name exactly as the
source states it. Never add a translation key outside the unit ids visible in fields_to_fix.

DIRECTED INPUT:
""" + _stable_json(payload)


def _normalize_contract_results(contract_results: Any) -> list[dict[str, Any]]:
    if not isinstance(contract_results, list):
        raise ValueError("contract results must be a list")
    normalized: list[dict[str, Any]] = []
    for result in contract_results:
        if not isinstance(result, Mapping):
            raise ValueError("each contract result requires passed bool and non-empty rationale")
        contract = result.get("contract")
        passed = result.get("passed")
        rationale = result.get("rationale")
        if (
            not isinstance(contract, str)
            or type(passed) is not bool
            or not isinstance(rationale, str)
            or not rationale.strip()
        ):
            raise ValueError("each contract result requires passed bool and non-empty rationale")
        normalized.append({"contract": contract, "passed": passed, "rationale": rationale.strip()})
    return normalized


def _validate_review_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise ValueError("review evidence must be an object")
    verdict = evidence.get("verdict")
    if verdict not in {"PASS", "FAIL"}:
        raise ValueError("review verdict must be PASS or FAIL")
    reviewed_at_stage = evidence.get("reviewed_at_stage")
    if reviewed_at_stage not in {"before_refinement", "after_refinement"}:
        raise ValueError("invalid review stage")
    refinement_requested = evidence.get("refinement_requested")
    if type(refinement_requested) is not bool:
        raise ValueError("refinement_requested must be a strict bool")

    contract_results = evidence.get("contract_results")
    if not isinstance(contract_results, list):
        raise ValueError("contract_results must contain each semantic contract exactly once")
    normalized_results = _normalize_contract_results(contract_results)
    result_by_contract = {result["contract"]: result for result in normalized_results}
    if len(result_by_contract) != len(normalized_results) or set(result_by_contract) != set(
        SEMANTIC_REVIEW_CONTRACTS
    ):
        raise ValueError("contract_results must contain each semantic contract exactly once")
    checked_contracts = list(SEMANTIC_REVIEW_CONTRACTS)
    normalized_results = [result_by_contract[contract] for contract in checked_contracts]
    supplied_checked = evidence.get("checked_contracts")
    if supplied_checked is not None and supplied_checked != checked_contracts:
        raise ValueError("checked_contracts must be derived from contract_results")

    issues = evidence.get("issues")
    remaining_issues = evidence.get("remaining_issues")
    if not isinstance(issues, list) or not isinstance(remaining_issues, list):
        raise ValueError("review issue fields must be lists")
    normalized_issues: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, Mapping):
            raise ValueError("review issues must be directed objects")
        contract = issue.get("contract")
        field = issue.get("field")
        problem = issue.get("problem")
        if (
            contract not in SEMANTIC_REVIEW_CONTRACTS
            or not isinstance(field, str)
            or not field.strip()
            or not isinstance(problem, str)
            or not problem.strip()
        ):
            raise ValueError("each review issue contract, field, and problem must be directed")
        normalized_issues.append(dict(issue))
    if any(not isinstance(issue, str) or not issue.strip() for issue in remaining_issues):
        raise ValueError("remaining_issues must contain non-empty strings")

    failed = [result for result in normalized_results if not result["passed"]]
    failed_contracts = {result["contract"] for result in failed}
    issue_contracts = {issue["contract"] for issue in normalized_issues}
    if verdict == "PASS":
        if issues:
            raise ValueError("PASS review requires empty issues")
        if remaining_issues:
            raise ValueError("PASS review requires empty remaining_issues")
        if failed:
            raise ValueError("PASS review requires every contract result to pass")
    else:
        if not failed:
            raise ValueError("FAIL review requires a failed contract result")
        if issue_contracts - failed_contracts:
            raise ValueError("review issue cannot point to a passed contract")
        if failed_contracts - issue_contracts:
            raise ValueError("every failed contract requires a directed issue")
        if not issues:
            raise ValueError("FAIL review requires directed issues")
        if not remaining_issues:
            raise ValueError("FAIL review requires remaining_issues")
    if reviewed_at_stage == "before_refinement" and refinement_requested != (verdict == "FAIL"):
        raise ValueError("before-refinement request must match verdict")
    if reviewed_at_stage == "after_refinement" and refinement_requested:
        raise ValueError("a second refinement is not permitted")

    normalized = {
        "verdict": verdict,
        "issues": normalized_issues,
        "remaining_issues": list(remaining_issues),
        "contract_results": normalized_results,
        "checked_contracts": checked_contracts,
        "reviewed_at_stage": reviewed_at_stage,
        "refinement_requested": refinement_requested,
    }
    try:
        json.dumps(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError("review evidence must be JSON serializable") from exc
    return normalized


def make_review_evidence(
    *,
    verdict: str,
    issues: Sequence[Mapping[str, Any]],
    remaining_issues: Sequence[str],
    contract_results: Sequence[Mapping[str, Any]],
    reviewed_at_stage: str,
    refinement_requested: bool,
    checked_contracts: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate and return the complete persisted semantic-review evidence."""
    evidence = {
        "verdict": verdict,
        "issues": list(issues),
        "remaining_issues": list(remaining_issues),
        "contract_results": list(contract_results),
        "reviewed_at_stage": reviewed_at_stage,
        "refinement_requested": refinement_requested,
    }
    if checked_contracts is not None:
        evidence["checked_contracts"] = list(checked_contracts)
    return _validate_review_evidence(evidence)


def build_refinement_evidence(
    *,
    review_before_refinement: Mapping[str, Any],
    fields_to_fix: Mapping[str, Any],
    refinement_patch: Mapping[str, Any],
    rechecked_contract_results: Sequence[Mapping[str, Any]],
    remaining_issues: Sequence[Mapping[str, Any]],
    hard_gate_replay: Mapping[str, Any],
    prior_refinement_count: int,
) -> dict[str, Any]:
    """Merge the sole directed recheck with inherited before-review evidence."""
    if (
        not isinstance(prior_refinement_count, int)
        or isinstance(prior_refinement_count, bool)
        or prior_refinement_count != 0
    ):
        raise RuntimeError("second refinement is not permitted")
    before = _validate_review_evidence(review_before_refinement)
    if before["reviewed_at_stage"] != "before_refinement" or before["verdict"] != "FAIL":
        raise ValueError("refinement requires a complete failing before-review")
    if (
        not isinstance(fields_to_fix, Mapping)
        or not fields_to_fix
        or any(not isinstance(field, str) or not field.strip() for field in fields_to_fix)
    ):
        raise ValueError("fields_to_fix must be a non-empty object")
    if not isinstance(refinement_patch, Mapping) or not refinement_patch:
        raise ValueError("refinement_patch must be a non-empty object")
    if not set(refinement_patch) <= set(fields_to_fix):
        raise ValueError("refinement_patch contains fields outside fields_to_fix")

    failed_contracts = {
        result["contract"] for result in before["contract_results"] if not result["passed"]
    }
    normalized_rechecks = _normalize_contract_results(list(rechecked_contract_results))
    recheck_by_contract = {result["contract"]: result for result in normalized_rechecks}
    if (
        len(recheck_by_contract) != len(normalized_rechecks)
        or set(recheck_by_contract) != failed_contracts
    ):
        raise ValueError("rechecked_contract_results must exactly cover failed contracts")

    if not isinstance(remaining_issues, Sequence) or isinstance(remaining_issues, str | bytes):
        raise ValueError("remaining_issues must be directed issue objects")
    if any(not isinstance(issue, Mapping) for issue in remaining_issues):
        raise ValueError("remaining_issues must be directed issue objects")
    directed_remaining = [dict(issue) for issue in remaining_issues]
    merged_results = [
        (
            recheck_by_contract[result["contract"]]
            if result["contract"] in recheck_by_contract
            else result
        )
        for result in before["contract_results"]
    ]
    still_failed = any(not result["passed"] for result in merged_results)
    after = make_review_evidence(
        verdict="FAIL" if still_failed else "PASS",
        issues=directed_remaining,
        remaining_issues=[
            issue.get("problem", "") if isinstance(issue, Mapping) else ""
            for issue in directed_remaining
        ],
        contract_results=merged_results,
        reviewed_at_stage="after_refinement",
        refinement_requested=False,
    )
    if not isinstance(hard_gate_replay, Mapping) or not isinstance(
        hard_gate_replay.get("all_passed"), bool
    ):
        raise ValueError("hard-gate replay must record all_passed")
    if after["verdict"] == "PASS" and hard_gate_replay["all_passed"] is not True:
        raise ValueError("PASS after-review requires hard gates to pass")
    evidence = {
        "review_before_refinement": before,
        "fields_to_fix": dict(fields_to_fix),
        "refinement_patch": dict(refinement_patch),
        "rechecked_contract_results": normalized_rechecks,
        "review_after_refinement": after,
        "hard_gate_replay": dict(hard_gate_replay),
        "refinement_count": 1,
    }
    try:
        json.dumps(evidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("refinement evidence must be JSON serializable") from exc
    return evidence


def run_prototype_dry_run(
    invoke: Callable[[str, str], Mapping[str, Any]],
    prompts: Mapping[str, str],
    *,
    refinement_fields_to_fix: Mapping[str, Any] | None = None,
    refinement_evidence_context: Mapping[str, Any] | None = None,
    hard_gate_replay: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Exercise the fixed four-call path and optional sole refinement offline."""
    calls: list[dict[str, Any]] = []
    responses: dict[str, Any] = {}
    for stage in ("blueprint", "language_support", "translation", "semantic_review"):
        prompt = prompts.get(stage)
        if not isinstance(prompt, str):
            raise ValueError(f"missing prompt for {stage}")
        response = invoke(stage, prompt)
        if not isinstance(response, Mapping):
            raise ValueError(f"{stage} response must be an object")
        if stage == "blueprint" and (
            response.get("article_type") not in TRANSFER_TASK_KIND_BY_ARTICLE_TYPE
            or response.get("effective_difficulty") not in {"B1", "B2", "C1"}
        ):
            raise ValueError("blueprint response lacks required prototype fields")
        if stage == "language_support" and any(
            not isinstance(response.get(field), list)
            for field in ("language_targets", "sentence_maps", "high_difficulty_unit_ids")
        ):
            raise ValueError("language_support response lacks required prototype fields")
        if stage == "translation" and not isinstance(response.get("translations"), list):
            raise ValueError("translation response lacks required prototype fields")
        calls.append({"stage": stage, "prompt_chars": len(prompt)})
        responses[stage] = dict(response)

    try:
        review = make_review_evidence(**responses["semantic_review"])
    except (TypeError, ValueError) as exc:
        raise ValueError("semantic_review response is incomplete") from exc
    responses["semantic_review"] = review

    refinement_evidence = None
    if review["verdict"] == "FAIL":
        if not isinstance(refinement_fields_to_fix, Mapping):
            raise ValueError("missing refinement_fields_to_fix")
        if not isinstance(refinement_evidence_context, Mapping):
            raise ValueError("missing refinement_evidence_context")
        prompt = build_refinement_prompt(
            review,
            refinement_fields_to_fix,
            refinement_evidence_context,
        )
        refinement = invoke("refinement", prompt)
        if not isinstance(refinement, Mapping):
            raise ValueError("refinement response must be an object")
        expected_keys = {
            "refinement_patch",
            "rechecked_contract_results",
            "remaining_issues",
        }
        if set(refinement) != expected_keys:
            raise ValueError("refinement response contains unexpected fields")
        calls.append({"stage": "refinement", "prompt_chars": len(prompt)})
        if not isinstance(hard_gate_replay, Mapping):
            raise ValueError("missing hard_gate_replay")
        try:
            refinement_evidence = build_refinement_evidence(
                review_before_refinement=review,
                fields_to_fix=refinement_fields_to_fix,
                refinement_patch=refinement["refinement_patch"],
                rechecked_contract_results=refinement["rechecked_contract_results"],
                remaining_issues=refinement["remaining_issues"],
                hard_gate_replay=hard_gate_replay,
                prior_refinement_count=0,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("refinement response is incomplete") from exc
        responses["refinement"] = refinement_evidence

    return {
        "logical_call_count": len(calls),
        "calls": calls,
        "prompt_chars": {call["stage"]: call["prompt_chars"] for call in calls},
        "responses": responses,
        "refinement_evidence": refinement_evidence,
    }


def transfer_task_kind(article_type: str) -> str:
    """Return the only valid transfer task kind for an article type."""
    try:
        return TRANSFER_TASK_KIND_BY_ARTICLE_TYPE[article_type]
    except KeyError:
        raise ValueError(f"unknown article_type: {article_type!r}") from None


def derive_translation_unit_ids(
    effective_difficulty: str,
    reading_units: Sequence[Mapping[str, Any]],
    *,
    substantive_unit_ids: Sequence[str],
    checkpoint_evidence_ids: Sequence[str] = (),
    language_target_paragraph_ids: Sequence[str] = (),
    sentence_map_paragraph_ids: Sequence[str] = (),
    high_difficulty_unit_ids: Sequence[str] = (),
) -> list[str]:
    """Derive translation targets from deterministic, non-Gold inputs."""
    ordered_ids = [unit.get("id") for unit in reading_units]
    if any(not isinstance(unit_id, str) or not unit_id for unit_id in ordered_ids):
        raise ValueError("every reading unit must have a non-empty string id")
    if len(ordered_ids) != len(set(ordered_ids)):
        raise ValueError("reading unit ids must be unique")
    substantive = set(substantive_unit_ids)
    if not substantive <= set(ordered_ids):
        raise ValueError("substantive_unit_ids contains an unknown paragraph id")
    if effective_difficulty == "B1":
        return [unit_id for unit_id in ordered_ids if unit_id in substantive]
    if effective_difficulty == "B2":
        selected = (
            set(checkpoint_evidence_ids)
            | set(language_target_paragraph_ids)
            | set(sentence_map_paragraph_ids)
        )
        if not selected <= substantive:
            raise ValueError("B2 translation anchor contains a non-substantive paragraph id")
        return [unit_id for unit_id in ordered_ids if unit_id in selected]
    if effective_difficulty == "C1":
        referenced = (
            set(checkpoint_evidence_ids)
            | set(language_target_paragraph_ids)
            | set(sentence_map_paragraph_ids)
            | set(high_difficulty_unit_ids)
        )
        if not referenced <= substantive:
            raise ValueError("C1 teaching anchor contains a non-substantive paragraph id")
        selected = set(high_difficulty_unit_ids) | set(sentence_map_paragraph_ids)
        return [unit_id for unit_id in ordered_ids if unit_id in selected]
    raise ValueError(f"unknown effective_difficulty: {effective_difficulty!r}")
