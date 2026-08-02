"""Neutral grammar-tag normalization and label extraction helpers."""

from __future__ import annotations

import re

# Alias merge map: common LLM variations → canonical form.
# NOTE: ``relative_clause`` is intentionally not aliased: it remains a valid
# generic tag when the specific subtype is unknown.
_TAG_ALIASES: dict[str, str] = {
    "defining_relative_clause": "restrictive_relative_clause",
    "limiting_relative_clause": "restrictive_relative_clause",
    "non_defining_relative_clause": "nonrestrictive_relative_clause",
    "non-defining_relative_clause": "nonrestrictive_relative_clause",
    "participle_adverbial": "past_participle_adverbial",
    "participle_attribute": "past_participle_attribute",
    "fronting": "subject_clause_fronting",
}


def _normalize_tag(raw: str) -> str | None:
    """Normalize one grammar tag to the shared canonical form."""

    tag = raw.strip().lower()
    tag = re.sub(r"[\s\-_]+", "_", tag).strip("_")
    if not tag:
        return None
    tag = _TAG_ALIASES.get(tag, tag)
    if tag in {"general", "complex", "other", "misc"}:
        return None
    return tag


def normalize_grammar_tags(tags: list[str]) -> list[str]:
    """Normalize and deduplicate a list of grammar tags."""

    seen: set[str] = set()
    result: list[str] = []
    for raw in tags:
        tag = _normalize_tag(raw)
        if tag is not None and tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result


# Order matters: more specific patterns must come before less specific ones.
_LABEL_TAG_PATTERNS: list[tuple[str, list[str]]] = [
    ("非限制性定语从句", ["nonrestrictive_relative_clause"]),
    ("同位语从句", ["appositive_clause"]),
    ("过去分词后置定语", ["past_participle_attribute"]),
    ("过去分词作状语", ["past_participle_adverbial"]),
    ("过去分词状语", ["past_participle_adverbial"]),
    ("分词结果状语", ["past_participle_adverbial"]),
    ("现在分词状语", ["present_participle_adverbial"]),
    ("名词性从句", ["object_clause"]),
    ("介词+关系代词", ["restrictive_relative_clause"]),
    ("限制性定语从句", ["restrictive_relative_clause"]),
    ("否定副词前置", ["inversion"]),
    ("虚拟条件句倒装", ["inversion"]),
    ("虚拟倒装", ["inversion"]),
    ("倒装结构", ["inversion"]),
    ("主句插入", ["main_clause_interruption"]),
    ("动词并列", ["parallelism"]),
    ("明喻", ["parallelism"]),
    ("give up", ["nonfinite"]),
    ("not only", ["inversion", "parallelism"]),
]

_GENERAL_TAG_PATTERNS: list[tuple[str, list[str]]] = [
    ("宾语从句", ["object_clause"]),
    ("定语从句", ["relative_clause"]),
    ("倒装", ["inversion"]),
    ("被动语态", ["passive_voice"]),
    ("插入", ["main_clause_interruption"]),
    ("反复", ["parallelism"]),
    ("让步", ["nested_clause"]),
    ("转折", ["nested_clause"]),
]


def _rule_extract_grammar_tags(label: str, output_type: str) -> list[str]:
    """Extract normalized grammar tags from a Chinese example label."""

    tags: set[str] = set()
    for pattern, tag_list in _LABEL_TAG_PATTERNS:
        if pattern in label:
            tags.update(tag_list)
    for pattern, tag_list in _GENERAL_TAG_PATTERNS:
        if pattern in label:
            tags.update(tag_list)
    if output_type == "sentence_analysis" and (
        "定语从句" in label or "宾语从句" in label
    ):
        tags.add("nested_clause")
    if not tags:
        tags.add("unclassified")
    return sorted(tags)
