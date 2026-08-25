"""Deterministic teaching-contract validation (shared, gold-free).

Migrated verbatim from the evals teaching-v2 prototype: an empty issue
list means pass. All functions are pure and offline.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.daily_reader.teaching.normalize import normalize_text

TRANSFER_TASK_KIND_BY_ARTICLE_TYPE = {
    "news_report": "retell",
    "opinion_commentary": "counter",
    "explainer": "explain",
    "narrative_profile": "rewrite",
}

TRANSFER_CONTENT_REQUIREMENT_VALUES = {
    "fact_chain",
    "original_stance",
    "mechanism_or_causality",
    "character_motivation",
    "scene_contrast",
    "quotation_characterization",
    "narrative_viewpoint",
}


def _normalized_text(value: Any) -> str:
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def _digit_value_keys(text: str) -> set[str]:
    """Numeric value keys for digit tokens. F-J2 rule A strips thousands
    separators ('5,700' -> '5700'); CJK 万/亿 scale to absolute values;
    rule C expands '20世纪80年代' decade forms to candidate years
    ('1980'/'2080')."""
    cleaned = text.replace(",", "")
    keys: set[str] = set()
    for token in re.findall(r"\d+(?:\.\d+)?(?:[万亿%])?", cleaned):
        bare = token.rstrip("%")
        scale = 1
        if bare.endswith("万"):
            scale = 10000
            bare = bare[:-1]
        elif bare.endswith("亿"):
            scale = 100000000
            bare = bare[:-1]
        try:
            value = float(bare) * scale
        except ValueError:
            continue
        keys.add(f"{value:g}")
        keys.add(token)
    for century, decade in re.findall(r"(\d{1,2})世纪(\d{2})年代", cleaned):
        c, d = int(century), int(decade)
        keys.update({str((c - 1) * 100 + d), str(c * 100 + d)})
    return keys


_MAGNITUDE_WORDS = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}


def _word_number_keys(text: str) -> set[str]:
    """English magnitude words as numeric values (F-J2 rule B:
    'half a million' -> 500000)."""
    keys: set[str] = set()
    for match in re.finditer(
        r"(half\s+a\s+|a\s+)?(hundred|thousand|million|billion)", text.casefold()
    ):
        base = _MAGNITUDE_WORDS[match.group(2)]
        prefix = (match.group(1) or "").strip()
        if prefix == "half a":
            keys.add(f"{base * 0.5:g}")
        elif prefix and prefix != "a":
            try:
                keys.add(f"{float(prefix) * base:g}")
            except ValueError:
                keys.add(f"{base:g}")
        else:
            keys.add(f"{base:g}")
    return keys


def _ungrounded_tokens(translation: str, source: str) -> list[str]:
    """Determinable fidelity subset (F-I3), normalized per F-J2 against the
    three P-4F7 measured false-positive classes:
    - rule A thousands separators ('at least 5,700 excess deaths' vs
      '至少有5700例超额死亡');
    - rule B magnitude conversion ('half a million people' vs '50万人');
    - rule C decade form ('since the 1980s' vs '从20世纪80年代起').
    Single digits stay ignored; fabricated numbers/names with no grounded
    counterpart on the source side (the bumble-u23 class: 无源数字/专名)
    remain flagged, as do altered years (2026->2022)."""
    folded = source.casefold()
    grounded_values = _digit_value_keys(source) | _word_number_keys(source)
    # Rule C needs the paired 'N世纪M年代' context: both digits are grounded
    # together when a candidate year appears on the source side.
    decade_grounded: set[str] = set()
    for century, decade in re.findall(r"(\d{1,2})世纪(\d{2})年代", translation):
        c, d = int(century), int(decade)
        candidates = {str((c - 1) * 100 + d), str(c * 100 + d)}
        if any(cand in folded or cand in grounded_values for cand in candidates):
            decade_grounded.update({century, decade})
    tokens: list[str] = []
    for token in re.findall(r"\d{2,4}(?:[.,]\d+)*(?:[万亿%])?", translation):
        if token in decade_grounded or token.rstrip("%") in decade_grounded:
            continue
        bare = token.rstrip("%").rstrip("万亿")
        literal_ok = bare in folded or any(
            part and part in folded for part in bare.replace(",", ".").split(".")
        )
        value_ok = bool(_digit_value_keys(token) & grounded_values)
        if not literal_ok and not value_ok:
            tokens.append(token)
    for word in re.findall(r"[A-Za-z][A-Za-z\-']{2,}", translation):
        if word.casefold() not in folded:
            tokens.append(word)
    return tokens


def validate_teaching_contract(
    blueprint: Mapping[str, Any],
    learning_package: Mapping[str, Any],
    *,
    reading_units: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, str]]:
    """Return deterministic teaching-contract issues; an empty list means pass.

    When ``reading_units`` is supplied:
    - a translation that merely repeats its source unit verbatim is reported
      as ``translation_source_echo`` (counterpart of the translation
      target-language contract);
    - a language-target expression or sentence-map sentence that is not a
      verbatim quote of its anchored unit is reported as
      ``teaching_anchor_not_verbatim`` (same normalization as the
      anchors_resolve gate: normalize_text for expressions, whitespace
      squash for sentences);
    - a translation containing multi-digit numbers or Latin-script words
      absent from its anchored unit is reported as
      ``translation_source_mismatch`` (the determinable subset of
      translation fidelity; semantic fabrication without literal traces
      stays with review/Judge/human).
    """
    issues: list[dict[str, str]] = []
    sections: dict[str, list[Any]] = {}
    for field, minimum, maximum, code in (
        ("language_targets", 3, 5, "language_target_count"),
        ("sentence_maps", 1, 2, "sentence_map_count"),
        ("comprehension_checkpoints", 2, 4, "checkpoint_count"),
    ):
        value = learning_package.get(field)
        count = len(value) if isinstance(value, list) else -1
        sections[field] = value if isinstance(value, list) else []
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
    actual_content = task.get("content_requirement") if isinstance(task, Mapping) else None
    if actual_content not in TRANSFER_CONTENT_REQUIREMENT_VALUES:
        issues.append(
            {
                "code": "transfer_content_metadata_invalid",
                "field": "transfer_task.content_requirement",
                "detail": (
                    "content_requirement must use a declared enum; semantic review checks fit"
                ),
            }
        )
    if blueprint.get("reading_mission_stance") != "neutral":
        issues.append(
            {
                "code": "reading_mission_stance_metadata_invalid",
                "field": "reading_mission_stance",
                "detail": "neutral must be declared; semantic review checks the mission text",
            }
        )
    expressions = {
        target.get("expression")
        for target in sections["language_targets"]
        if isinstance(target, Mapping) and isinstance(target.get("expression"), str)
    }
    required = (
        task.get("required_language_target_expressions", []) if isinstance(task, Mapping) else []
    )
    if (
        not isinstance(required, list)
        or any(not isinstance(expression, str) or not expression for expression in required)
        or not expressions.intersection(required)
    ):
        issues.append(
            {
                "code": "transfer_expression_not_taught",
                "field": "transfer_task.required_language_target_expressions",
                "detail": "transfer task must require at least one taught language target",
            }
        )
    for index, checkpoint in enumerate(sections["comprehension_checkpoints"]):
        if not isinstance(checkpoint, Mapping):
            issues.append(
                {
                    "code": "checkpoint_subject_metadata_invalid",
                    "field": f"comprehension_checkpoints[{index}]",
                    "detail": "checkpoint subject metadata must be an object",
                }
            )
            continue
        prompt_subject = checkpoint.get("prompt_subject")
        answer_subject = checkpoint.get("reference_answer_subject")
        if not (
            isinstance(prompt_subject, str)
            and prompt_subject.strip()
            and isinstance(answer_subject, str)
            and answer_subject.strip()
        ):
            issues.append(
                {
                    "code": "checkpoint_subject_metadata_invalid",
                    "field": f"comprehension_checkpoints[{index}]",
                    "detail": (
                        "both declared subjects are required; semantic review checks consistency"
                    ),
                }
            )
    if blueprint.get("effective_difficulty") == "C1":
        for index, sentence_map in enumerate(sections["sentence_maps"]):
            complexity = (
                sentence_map.get("complexity_kind") if isinstance(sentence_map, Mapping) else None
            )
            if complexity not in {"complex_syntax", "argument_structure"}:
                issues.append(
                    {
                        "code": "c1_sentence_map_complexity_metadata_invalid",
                        "field": f"sentence_maps[{index}].complexity_kind",
                        "detail": (
                            "C1 requires a complexity enum; semantic review checks "
                            "actual complexity"
                        ),
                    }
                )
    for target_index, target in enumerate(sections["language_targets"]):
        if not isinstance(target, Mapping) or not isinstance(target.get("expression"), str):
            issues.append(
                {
                    "code": "language_target_metadata_invalid",
                    "field": f"language_targets[{target_index}]",
                    "detail": "language target must declare an expression and metadata",
                }
            )
            continue
        if not isinstance(target.get("target_kind"), str) or not target["target_kind"].strip():
            issues.append(
                {
                    "code": "language_target_metadata_invalid",
                    "field": f"language_targets[{target_index}].target_kind",
                    "detail": "target_kind is required; semantic review checks transfer value",
                }
            )
        expression = " ".join(target["expression"].split()).casefold()
        for map_index, sentence_map in enumerate(sections["sentence_maps"]):
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
    if reading_units:
        source_texts = {
            unit.get("id"): _normalized_text(unit.get("text"))
            for unit in reading_units
            if isinstance(unit, Mapping)
        }
        source_raw = {
            unit.get("id"): unit.get("text") for unit in reading_units if isinstance(unit, Mapping)
        }
        translations = learning_package.get("translations_by_paragraph_id")
        if isinstance(translations, Mapping):
            for paragraph_id, text in translations.items():
                normalized = _normalized_text(text)
                if normalized and normalized == source_texts.get(paragraph_id):
                    issues.append(
                        {
                            "code": "translation_source_echo",
                            "field": f"translations_by_paragraph_id.{paragraph_id}",
                            "detail": (
                                "translation repeats the source unit verbatim instead of "
                                "rendering it in the target language"
                            ),
                        }
                    )
                source_unit = source_raw.get(paragraph_id)
                if (
                    isinstance(text, str)
                    and text.strip()
                    and isinstance(source_unit, str)
                    and source_unit.strip()
                ):
                    tokens = _ungrounded_tokens(text, source_unit)
                    if tokens:
                        issues.append(
                            {
                                "code": "translation_source_mismatch",
                                "field": f"translations_by_paragraph_id.{paragraph_id}",
                                "detail": (
                                    "translation contains tokens absent from the anchored "
                                    "unit: " + ", ".join(tokens)
                                ),
                            }
                        )
        for index, target in enumerate(sections["language_targets"]):
            if not isinstance(target, Mapping) or not isinstance(target.get("expression"), str):
                continue
            expr = normalize_text(target["expression"])
            if expr and expr not in normalize_text(source_raw.get(target.get("paragraph_id"))):
                issues.append(
                    {
                        "code": "teaching_anchor_not_verbatim",
                        "field": f"language_targets[{index}]",
                        "detail": (
                            "target expression is not a verbatim quote of its anchored "
                            "unit (whitespace/case normalized)"
                        ),
                    }
                )
        for index, sentence_map in enumerate(sections["sentence_maps"]):
            if not isinstance(sentence_map, Mapping) or not isinstance(
                sentence_map.get("sentence"), str
            ):
                continue
            squashed = re.sub(r"\s+", "", sentence_map["sentence"])
            unit_text = source_raw.get(sentence_map.get("paragraph_id"))
            haystack = re.sub(r"\s+", "", unit_text) if isinstance(unit_text, str) else ""
            if squashed and squashed not in haystack:
                issues.append(
                    {
                        "code": "teaching_anchor_not_verbatim",
                        "field": f"sentence_maps[{index}]",
                        "detail": (
                            "sentence-map sentence is not a verbatim quote of its "
                            "anchored unit (whitespace squashed)"
                        ),
                    }
                )
    return issues
