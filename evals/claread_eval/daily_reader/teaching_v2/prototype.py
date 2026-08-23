"""Offline-only teaching v2 prototype contracts."""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

TRANSFER_TASK_KIND_BY_ARTICLE_TYPE = {
    "news_report": "retell",
    "opinion_commentary": "counter",
    "explainer": "explain",
    "narrative_profile": "rewrite",
}

TRANSFER_CONTENT_REQUIREMENTS = {
    "news_report": {"fact_chain"},
    "opinion_commentary": {"original_stance"},
    "explainer": {"mechanism_or_causality"},
    "narrative_profile": {
        "character_motivation",
        "scene_contrast",
        "quotation_characterization",
        "narrative_viewpoint",
    },
}

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
news_report, opinion_commentary, explainer, narrative_profile. Set reading_mission_stance to
neutral: the mission must not prescribe which side the reader should support. Identify core
evidence, candidate transferable language, and genuinely difficult unit ids. Do not generate
translations or detailed language explanations in this stage.

Produce 2-4 evidence checkpoints with prompt_subject and reference_answer_subject for audit.
Produce exactly one transfer task using this fixed mapping: news_report=retell,
opinion_commentary=counter, explainer=explain, narrative_profile=rewrite. Its
required_language_target_expressions must name at least one expression for language support to
teach. Its content_requirement must be fact_chain, original_stance, mechanism_or_causality, or
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
rhetoric, and discourse links; reject ordinary complete fact sentences. Each target includes
target_kind and teaching_purpose. Return 1-2 sentence maps. For C1, every map must be either
complex_syntax or argument_structure and must explain why. Return explicit
high_difficulty_unit_ids. Do not add fields used by evaluation answers.

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

Return one translation for each supplied unit id. Never invent or add a paragraph id. When a
sentence map belongs to a target unit, reuse its single canonical translation verbatim inside
the unit translation.

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
    }
    _assert_generation_safe(payload)
    return """Audit the lesson and persist substantive review evidence.

Check factual fidelity, checkpoint subject consistency, evidence anchors, difficulty fit,
translation selection, transfer mapping and content, repeated teaching points, language-target
value, and mission neutrality. Even for PASS, checked_contracts must list every checked contract;
never replace the audit with a bare verdict. Return verdict, issues, remaining_issues,
checked_contracts, reviewed_at_stage='before_refinement', and refinement_requested. FAIL must
include directed issues.

REVIEW INPUT:
""" + _stable_json(payload)


def build_refinement_prompt(
    issues: Sequence[Mapping[str, Any]], fields_to_fix: Mapping[str, Any]
) -> str:
    """Build the sole directed-refinement prompt without unrelated content."""
    payload = {"fields_to_fix": fields_to_fix, "issues": issues}
    _assert_generation_safe(payload)
    return """Perform the one permitted directed refinement.

Modify only fields_to_fix in response to issues; do not rewrite unrelated fields. Return a
refinement_patch plus a complete review_after_refinement containing verdict, issues,
remaining_issues, checked_contracts, reviewed_at_stage='after_refinement', and
refinement_requested=false. No second refinement is permitted.

DIRECTED INPUT:
""" + _stable_json(payload)


def make_review_evidence(
    *,
    verdict: str,
    issues: Sequence[Mapping[str, Any]],
    remaining_issues: Sequence[str],
    checked_contracts: Sequence[str],
    reviewed_at_stage: str,
    refinement_requested: bool,
) -> dict[str, Any]:
    """Validate and return the complete persisted semantic-review evidence."""
    if verdict not in {"PASS", "FAIL"}:
        raise ValueError("review verdict must be PASS or FAIL")
    if reviewed_at_stage not in {"before_refinement", "after_refinement"}:
        raise ValueError("invalid review stage")
    if set(checked_contracts) != set(SEMANTIC_REVIEW_CONTRACTS) or len(checked_contracts) != len(
        SEMANTIC_REVIEW_CONTRACTS
    ):
        raise ValueError("checked_contracts must contain the complete contract exactly once")
    if verdict == "FAIL" and not issues:
        raise ValueError("FAIL review requires directed issues")
    if reviewed_at_stage == "before_refinement" and refinement_requested != (verdict == "FAIL"):
        raise ValueError("before-refinement request must match verdict")
    if reviewed_at_stage == "after_refinement" and refinement_requested:
        raise ValueError("a second refinement is not permitted")
    evidence = {
        "verdict": verdict,
        "issues": [dict(issue) for issue in issues],
        "remaining_issues": list(remaining_issues),
        "checked_contracts": list(checked_contracts),
        "reviewed_at_stage": reviewed_at_stage,
        "refinement_requested": refinement_requested,
    }
    try:
        json.dumps(evidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("review evidence must be JSON serializable") from exc
    return evidence


def build_refinement_evidence(
    *,
    review_before_refinement: Mapping[str, Any],
    refinement_patch: Mapping[str, Any],
    review_after_refinement: Mapping[str, Any],
    hard_gate_replay: Mapping[str, Any],
    prior_refinement_count: int,
) -> dict[str, Any]:
    """Persist the sole refinement and its before/after audit evidence."""
    if (
        not isinstance(prior_refinement_count, int)
        or isinstance(prior_refinement_count, bool)
        or prior_refinement_count != 0
    ):
        raise RuntimeError("second refinement is not permitted")
    if (
        review_before_refinement.get("reviewed_at_stage") != "before_refinement"
        or review_before_refinement.get("verdict") != "FAIL"
        or review_before_refinement.get("refinement_requested") is not True
    ):
        raise ValueError("refinement requires a complete failing before-review")
    if (
        review_after_refinement.get("reviewed_at_stage") != "after_refinement"
        or review_after_refinement.get("refinement_requested") is not False
    ):
        raise ValueError("after-review must close refinement")
    if not refinement_patch:
        raise ValueError("refinement_patch must name directed fields")
    if not isinstance(hard_gate_replay.get("all_passed"), bool):
        raise ValueError("hard-gate replay must record all_passed")
    evidence = {
        "review_before_refinement": dict(review_before_refinement),
        "refinement_patch": dict(refinement_patch),
        "review_after_refinement": dict(review_after_refinement),
        "hard_gate_replay": dict(hard_gate_replay),
        "refinement_count": 1,
    }
    try:
        json.dumps(evidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("refinement evidence must be JSON serializable") from exc
    return evidence


def run_prototype_dry_run(
    invoke: Callable[[str, str], Mapping[str, Any]], prompts: Mapping[str, str]
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
        calls.append({"stage": stage, "prompt_chars": len(prompt)})
        responses[stage] = dict(response)

    try:
        review = make_review_evidence(**responses["semantic_review"])
    except (TypeError, ValueError) as exc:
        raise ValueError("semantic_review response is incomplete") from exc
    responses["semantic_review"] = review

    refinement_evidence = None
    if review["verdict"] == "FAIL":
        prompt = prompts.get("refinement")
        if not isinstance(prompt, str):
            raise ValueError("missing prompt for refinement")
        refinement = invoke("refinement", prompt)
        if not isinstance(refinement, Mapping):
            raise ValueError("refinement response must be an object")
        calls.append({"stage": "refinement", "prompt_chars": len(prompt)})
        try:
            refinement_evidence = build_refinement_evidence(
                review_before_refinement=review,
                refinement_patch=refinement["refinement_patch"],
                review_after_refinement=refinement["review_after_refinement"],
                hard_gate_replay=refinement["hard_gate_replay"],
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


def validate_teaching_contract(
    blueprint: Mapping[str, Any], learning_package: Mapping[str, Any]
) -> list[dict[str, str]]:
    """Return deterministic teaching-contract issues; an empty list means pass."""
    issues: list[dict[str, str]] = []
    for field, minimum, maximum, code in (
        ("language_targets", 3, 5, "language_target_count"),
        ("sentence_maps", 1, 2, "sentence_map_count"),
        ("comprehension_checkpoints", 2, 4, "checkpoint_count"),
    ):
        value = learning_package.get(field)
        count = len(value) if isinstance(value, list) else -1
        if not minimum <= count <= maximum:
            issues.append(
                {
                    "code": code,
                    "field": field,
                    "detail": f"{field} count {count} is outside {minimum}-{maximum}",
                }
            )
    for field in (
        "focus_questions",
        "micro_summaries",
        "full_translation",
        "article_translation",
        "paragraph_summaries",
    ):
        if field in blueprint or field in learning_package:
            issues.append(
                {
                    "code": "dense_teaching_field",
                    "field": field,
                    "detail": (
                        "per-paragraph questions/summaries and whole-article translation "
                        "are out of scope"
                    ),
                }
            )
    task = learning_package.get("transfer_task")
    if not isinstance(task, Mapping):
        issues.append(
            {
                "code": "transfer_task_count",
                "field": "transfer_task",
                "detail": "transfer_task must be exactly one object",
            }
        )
    article_type_value = blueprint.get("article_type")
    article_type = article_type_value if isinstance(article_type_value, str) else ""
    expected_kind = TRANSFER_TASK_KIND_BY_ARTICLE_TYPE.get(article_type)
    actual_kind = task.get("task_kind") if isinstance(task, Mapping) else None
    if expected_kind is None or actual_kind != expected_kind:
        issues.append(
            {
                "code": "transfer_task_kind_mismatch",
                "field": "transfer_task.task_kind",
                "detail": f"{article_type!r} requires {expected_kind!r}, got {actual_kind!r}",
            }
        )
    expected_content = TRANSFER_CONTENT_REQUIREMENTS.get(article_type, set())
    actual_content = task.get("content_requirement") if isinstance(task, Mapping) else None
    if actual_content not in expected_content:
        issues.append(
            {
                "code": "transfer_content_mismatch",
                "field": "transfer_task.content_requirement",
                "detail": f"{article_type!r} requires one of {sorted(expected_content)!r}",
            }
        )
    if blueprint.get("reading_mission_stance") != "neutral":
        issues.append(
            {
                "code": "reading_mission_not_neutral",
                "field": "reading_mission",
                "detail": "reading mission must not prescribe support for one side",
            }
        )
    expressions = {
        target.get("expression")
        for target in learning_package.get("language_targets", [])
        if isinstance(target, Mapping) and isinstance(target.get("expression"), str)
    }
    required = (
        task.get("required_language_target_expressions", []) if isinstance(task, Mapping) else []
    )
    if not isinstance(required, list) or not expressions.intersection(required):
        issues.append(
            {
                "code": "transfer_expression_not_taught",
                "field": "transfer_task.required_language_target_expressions",
                "detail": "transfer task must require at least one taught language target",
            }
        )
    for index, checkpoint in enumerate(learning_package.get("comprehension_checkpoints", [])):
        if not isinstance(checkpoint, Mapping):
            continue
        prompt_subject = checkpoint.get("prompt_subject")
        answer_subject = checkpoint.get("reference_answer_subject")
        if (
            isinstance(prompt_subject, str)
            and isinstance(answer_subject, str)
            and prompt_subject.strip().casefold() != answer_subject.strip().casefold()
        ):
            issues.append(
                {
                    "code": "checkpoint_subject_conflict",
                    "field": f"comprehension_checkpoints[{index}]",
                    "detail": (
                        f"prompt subject {prompt_subject!r} conflicts with answer subject "
                        f"{answer_subject!r}"
                    ),
                }
            )
    if blueprint.get("effective_difficulty") == "C1":
        for index, sentence_map in enumerate(learning_package.get("sentence_maps", [])):
            complexity = (
                sentence_map.get("complexity_kind") if isinstance(sentence_map, Mapping) else None
            )
            if complexity not in {"complex_syntax", "argument_structure"}:
                issues.append(
                    {
                        "code": "c1_sentence_map_not_complex",
                        "field": f"sentence_maps[{index}].complexity_kind",
                        "detail": "C1 sentence maps require complex syntax or argument structure",
                    }
                )
    for target_index, target in enumerate(learning_package.get("language_targets", [])):
        if not isinstance(target, Mapping) or not isinstance(target.get("expression"), str):
            continue
        if target.get("target_kind") == "fact_sentence":
            issues.append(
                {
                    "code": "low_value_language_target",
                    "field": f"language_targets[{target_index}]",
                    "detail": "an ordinary complete fact sentence is not a transferable expression",
                }
            )
        expression = " ".join(target["expression"].split()).casefold()
        for map_index, sentence_map in enumerate(learning_package.get("sentence_maps", [])):
            if not isinstance(sentence_map, Mapping) or not isinstance(
                sentence_map.get("sentence"), str
            ):
                continue
            sentence = " ".join(sentence_map["sentence"].split()).casefold()
            if expression == sentence and target.get("teaching_purpose") == sentence_map.get(
                "teaching_purpose"
            ):
                issues.append(
                    {
                        "code": "duplicate_language_target_sentence_map",
                        "field": f"language_targets[{target_index}],sentence_maps[{map_index}]",
                        "detail": "same full sentence repeats the same teaching purpose",
                    }
                )
    return issues
