"""Regression tests for the neutral grammar-tag normalization helpers."""

from __future__ import annotations

from app.services.prompting.rag.grammar_tag_normalization import (
    _rule_extract_grammar_tags,
    normalize_grammar_tags,
)


def test_normalize_grammar_tags_basic() -> None:
    assert normalize_grammar_tags(["inversion", "passive_voice"]) == [
        "inversion",
        "passive_voice",
    ]


def test_normalize_grammar_tags_alias_merge() -> None:
    assert normalize_grammar_tags(["defining_relative_clause"]) == [
        "restrictive_relative_clause"
    ]


def test_normalize_grammar_tags_relative_clause_stays_generic() -> None:
    assert normalize_grammar_tags(["relative_clause"]) == ["relative_clause"]


def test_normalize_grammar_tags_reject_generic() -> None:
    assert normalize_grammar_tags(["general", "complex", "inversion"]) == [
        "inversion"
    ]


def test_normalize_grammar_tags_snake_case_conversion() -> None:
    result = normalize_grammar_tags(["Passive Voice", "past-participle"])
    assert "passive_voice" in result
    assert "past_participle" in result


def test_normalize_grammar_tags_dedup_and_empty() -> None:
    assert normalize_grammar_tags(["inversion", "inversion", "inversion"]) == [
        "inversion"
    ]
    assert normalize_grammar_tags([]) == []


def test_rule_extract_generic_dingyu_maps_to_relative_clause() -> None:
    tags = _rule_extract_grammar_tags("定语从句", "grammar_note")
    assert "relative_clause" in tags
    assert "restrictive_relative_clause" not in tags


def test_rule_extract_specific_xianzhixing_maps_to_restrictive() -> None:
    tags = _rule_extract_grammar_tags("限制性定语从句", "grammar_note")
    assert "restrictive_relative_clause" in tags


def test_normalize_grammar_tags_collapses_repeated_underscores() -> None:
    assert normalize_grammar_tags(["participle__adverbial"]) == [
        "past_participle_adverbial"
    ]


def test_normalize_grammar_tags_mixed_separators_collapse() -> None:
    assert normalize_grammar_tags([" passive - voice "]) == ["passive_voice"]


def test_normalize_grammar_tags_three_underscores_collapse() -> None:
    assert normalize_grammar_tags(["past___participle_adverbial"]) == [
        "past_participle_adverbial"
    ]


def test_normalize_grammar_tags_matches_canonical_known_inputs() -> None:
    pairs = {
        "participle_adverbial": "past_participle_adverbial",
        "participle__adverbial": "past_participle_adverbial",
        "defining_relative_clause": "restrictive_relative_clause",
        "non_defining_relative_clause": "nonrestrictive_relative_clause",
        "non-defining_relative_clause": "nonrestrictive_relative_clause",
        "fronting": "subject_clause_fronting",
        "Relative Clause": "relative_clause",
        "general": None,
        "complex": None,
    }
    for raw, expected in pairs.items():
        result = normalize_grammar_tags([raw])
        if expected is None:
            assert result == [], f"expected '{raw}' to be rejected, got {result}"
        else:
            assert result == [expected]
