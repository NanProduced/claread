"""P-5D-R1: production stage-prompt payload assembly ≡ canonical builder.

Pins the WHOLE user-payload composition (header + serialized payload) of the
five production teaching composers against the shared canonical builders in
``teaching/prototype.py`` — not just the registry system-instruction prefix.
P-5D showed that a single missing payload key (``semantic_review_contracts``)
passes every system-prefix test yet fails production 10/10 at the review DTO.

Value-semantic differences are intentional production behaviour and get their
own explicit assertions (review ``original_text`` truncation).
"""

from __future__ import annotations

import json

from app.agents.daily_teaching_agents import (
    MAX_REVIEW_ORIGINAL_TEXT_CHARS,
    DailyBlueprintAgentDeps,
    DailyLanguageSupportAgentDeps,
    DailySemanticReviewAgentDeps,
    DailyTeachingRefinementAgentDeps,
    DailyTranslationAgentDeps,
    build_daily_blueprint_prompt,
    build_daily_language_support_prompt,
    build_daily_semantic_review_prompt,
    build_daily_teaching_refinement_prompt,
    build_daily_translation_prompt,
)
from app.services.daily_reader.teaching.prototype import (
    SEMANTIC_REVIEW_CONTRACTS,
    _stable_json,
    make_review_evidence,
)

# The canonical builders live verbatim in the shared module consumed by both
# sides; importing the prompt builders from it keeps this lock single-source.
from app.services.daily_reader.teaching.prototype import (
    build_blueprint_prompt as canonical_blueprint_prompt,
)
from app.services.daily_reader.teaching.prototype import (
    build_language_support_prompt as canonical_language_support_prompt,
)
from app.services.daily_reader.teaching.prototype import (
    build_refinement_prompt as canonical_refinement_prompt,
)
from app.services.daily_reader.teaching.prototype import (
    build_semantic_review_prompt as canonical_semantic_review_prompt,
)
from app.services.daily_reader.teaching.prototype import (
    build_translation_prompt as canonical_translation_prompt,
)


def _payload_of(prompt: str, header: str) -> dict:
    """Parse the user-payload block that follows the canonical section header."""
    marker = f"\n{header}\n"
    assert prompt.count(marker) == 1, f"header {header!r} must appear exactly once"
    tail = prompt.split(marker, 1)[1]
    return json.loads(tail)


def _reading_units() -> list[dict[str, str]]:
    return [
        {"id": "u01", "text": "The city council approved the plan on Tuesday."},
        {"id": "u02", "text": "Critics argued the costs were underestimated."},
        {"id": "u03", "text": "Supporters pointed to years of public consultation."},
    ]


def _blueprint() -> dict:
    return {
        "article_type": "news_report",
        "effective_difficulty": "B2",
        "title_zh": "市议会通过长期争议的改建计划",
        "subtitle_zh": "多年公众咨询后仍有人质疑成本",
        "tags_zh": ["城市", "公共政策"],
        "reading_mission": "找出计划内容、双方论点与关键数字。",
        "reading_mission_stance": "neutral",
        "learning_objectives": ["追踪支持与反对的论证结构"],
        "structure_map": [
            {"label": "决定", "function": "事件核心", "paragraph_ids": ["u01"]},
            {"label": "争议", "function": "对立观点", "paragraph_ids": ["u02", "u03"]},
        ],
        "selected_paragraph_ids": ["u01", "u02", "u03"],
        "comprehension_checkpoints": [],
        "transfer_task": {
            "task_kind": "retell",
            "content_requirement": "fact_chain",
            "required_language_target_expressions": ["pointed to"],
            "prompt": "",
            "scaffold": "",
            "reference_points": [],
        },
    }


def _language_support() -> dict:
    return {
        "language_targets": [
            {
                "expression": "pointed to",
                "paragraph_id": "u03",
                "target_kind": "phrasal_verb",
                "teaching_purpose": "归因引述",
                "meaning_zh": "指出、援引",
                "usage_note": "常用于转述理由或证据",
                "reusable_pattern": "X pointed to Y as evidence of Z",
            }
        ],
        "sentence_maps": [
            {
                "sentence": "Critics argued the costs were underestimated.",
                "paragraph_id": "u02",
                "translation": "批评者认为成本被低估了。",
                "complexity_kind": None,
                "teaching_purpose": "宾语从句",
            }
        ],
        "high_difficulty_unit_ids": ["u02"],
    }


def _failing_before_review() -> dict:
    contracts = list(SEMANTIC_REVIEW_CONTRACTS)
    results = [
        {"contract": c, "passed": c != "transfer_language_use", "rationale": f"rationale: {c}"}
        for c in contracts
    ]
    return make_review_evidence(
        verdict="FAIL",
        issues=[
            {
                "contract": "transfer_language_use",
                "field": "learning_package.transfer_task.prompt",
                "problem": "transfer prompt does not use a taught language target",
            }
        ],
        remaining_issues=["transfer_task must require at least one taught expression"],
        contract_results=results,
        reviewed_at_stage="before_refinement",
        refinement_requested=True,
    )


def test_blueprint_payload_matches_canonical():
    article = {
        "title": "Council approves contested redevelopment plan",
        "source": "guardian",
        "reading_units": _reading_units(),
    }
    production = build_daily_blueprint_prompt(DailyBlueprintAgentDeps(article=article))
    canonical = canonical_blueprint_prompt(article)
    assert _payload_of(production, "ARTICLE:") == _payload_of(canonical, "ARTICLE:")


def test_language_support_payload_matches_canonical():
    units = _reading_units()
    difficulty = "B2"
    production = build_daily_language_support_prompt(
        DailyLanguageSupportAgentDeps(selected_units=units, effective_difficulty=difficulty)
    )
    canonical = canonical_language_support_prompt(units, difficulty)
    assert _payload_of(production, "SELECTED INPUT:") == _payload_of(canonical, "SELECTED INPUT:")


def test_translation_payload_matches_canonical():
    target_units = _reading_units()[:2]
    sentence_maps = [
        {"paragraph_id": "u02", "sentence": "Critics argued the costs were underestimated."}
    ]
    difficulty = "B2"
    production = build_daily_translation_prompt(
        DailyTranslationAgentDeps(
            target_units=target_units,
            sentence_maps=sentence_maps,
            effective_difficulty=difficulty,
        )
    )
    canonical = canonical_translation_prompt(target_units, sentence_maps, difficulty)
    assert _payload_of(production, "TARGET INPUT:") == _payload_of(canonical, "TARGET INPUT:")


def test_semantic_review_payload_matches_canonical():
    """The P-5D root cause: production must carry semantic_review_contracts."""
    original_text = "\n\n".join(unit["text"] for unit in _reading_units())
    blueprint = _blueprint()
    package = _language_support()
    checks = {"derived_translation_unit_ids": ["u01"], "teaching_contract_issues": []}
    production = build_daily_semantic_review_prompt(
        DailySemanticReviewAgentDeps(
            original_text=original_text,
            blueprint=blueprint,
            learning_package=package,
            deterministic_checks=checks,
        )
    )
    canonical = canonical_semantic_review_prompt(original_text, blueprint, package, checks)
    production_payload = _payload_of(production, "REVIEW INPUT:")
    canonical_payload = _payload_of(canonical, "REVIEW INPUT:")
    # Key sets first for a precise failure message, then full value equality.
    assert set(production_payload) == set(canonical_payload)
    assert production_payload == canonical_payload
    # Provenance: the contract list comes from the shared module, not a copy.
    assert list(production_payload["semantic_review_contracts"]) == list(SEMANTIC_REVIEW_CONTRACTS)


def test_refinement_payload_matches_canonical():
    before = _failing_before_review()
    fields_to_fix = {
        "transfer_task": dict(_blueprint()["transfer_task"], prompt="用 taught 表达改写")
    }
    evidence_context = {"failed_contracts": ["transfer_language_use"]}
    production = build_daily_teaching_refinement_prompt(
        DailyTeachingRefinementAgentDeps(
            review_before_refinement=before,
            fields_to_fix=fields_to_fix,
            evidence_context=evidence_context,
        )
    )
    canonical = canonical_refinement_prompt(before, fields_to_fix, evidence_context)
    production_payload = _payload_of(production, "DIRECTED INPUT:")
    canonical_payload = _payload_of(canonical, "DIRECTED INPUT:")
    assert set(production_payload) == set(canonical_payload)
    assert production_payload == canonical_payload
    # failed_contracts is derived from the before-review on both sides.
    assert production_payload["failed_contracts"] == ["transfer_language_use"]


def test_semantic_review_truncates_long_original_text_intentionally():
    """Production-only cost bound: original_text is capped at MAX chars."""
    long_text = "x" * (MAX_REVIEW_ORIGINAL_TEXT_CHARS + 500)
    blueprint = _blueprint()
    package = _language_support()
    checks: dict[str, object] = {}
    production_payload = _payload_of(
        build_daily_semantic_review_prompt(
            DailySemanticReviewAgentDeps(
                original_text=long_text,
                blueprint=blueprint,
                learning_package=package,
                deterministic_checks=dict(checks),  # type: ignore[arg-type]
            )
        ),
        "REVIEW INPUT:",
    )
    assert production_payload["original_text"] == long_text[:MAX_REVIEW_ORIGINAL_TEXT_CHARS]
    # Canonical passes the text through untruncated; the divergence is the one
    # documented intentional difference between the two composers.
    canonical_payload = json.loads(
        canonical_semantic_review_prompt(long_text, blueprint, package, checks).split(
            "\nREVIEW INPUT:\n", 1
        )[1]
    )
    assert canonical_payload["original_text"] == long_text


def test_stage_payload_serialization_is_stable_json():
    """Both composers serialize via the shared sorted-keys stable serializer."""
    payload = {"b": 1, "a": ["x", "y"]}
    assert _stable_json(payload) == '{"a":["x","y"],"b":1}'
